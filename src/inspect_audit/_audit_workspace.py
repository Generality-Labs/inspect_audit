"""The auditor's box and the host's trusted copy of what is in it.

The box is the agent's only workspace, on Docker locally and on Kubernetes on Hawk
alike. The host keeps the inputs it staged and the logs its tools collected, and
reads explicitly selected findings evidence and stages immutable submitted bundles. Inspect's
checkpointing carries the box and the sample store across a crash.
"""

import io
import json
import logging
import tarfile
from pathlib import Path
from typing import Any

import anyio
import anyio.to_thread
import yaml
from inspect_ai.solver import Generate, Solver, TaskState, solver
from inspect_ai.tool import ToolError
from inspect_ai.util import sandbox

from .containers import EGRESS_POLICY

logger = logging.getLogger(__name__)


def configure(root: Path, image: str, resources: dict[str, Any] | None = None) -> tuple[str, str]:
    """The auditor's box as a Kubernetes pod: no mounts, no service account.

    Its size is the deployment's default for a sandbox's default service unless the
    operator gives Kubernetes `resources` (requests/limits): nothing is reserved that
    the audit did not ask for (James, 2026-11-12; FRICTION 80).
    """
    service: dict[str, Any] = {
        "image": image,
        "command": ["sleep", "infinity"],
        "workingDir": "/workspace",
    }
    if resources:
        service["resources"] = resources
    values = {
        "services": {"default": service},
        "automountServiceAccountToken": False,
    }
    path = root / "auditor.values.yaml"
    path.write_text(yaml.safe_dump(values) + EGRESS_POLICY)
    return "k8s", str(path)


def _destination(root: Path, name: str) -> str:
    settings = json.loads((root / "artifacts.json").read_text())
    # Hawk indexes artifacts under the current sample UUID, including on resume.
    key = settings["sample_uuid"]
    return f"{settings['artifact_dir'].rstrip('/')}/{key}/{name}"


def hawk_would_process(key: str) -> str | None:
    """Return the reason Hawk would process an object at this key, or None.

    Eval logs update warehouse records by eval ID; eval-set metadata and scan
    results also trigger processors. Keep this routing aligned with Hawk.
    """
    if key.startswith("evals/"):
        # the same order as Hawk's own routing: .keep, then .eval (bar .fast.eval), then
        # live buffers, then files at the eval set's root
        if key.endswith("/.keep"):
            return None
        if key.endswith(".eval"):
            if key.endswith(".fast.eval"):
                return None
            return "Hawk imports every .eval under evals/ into the shared warehouse"
        if "/.buffer/" in key:
            return None
        eval_set, _, rest = key.removeprefix("evals/").partition("/")
        if eval_set and rest and "/" not in rest:
            return "Hawk reads a file directly in evals/<eval set>/ as that set's metadata"
    if key.startswith("scans/") and key.endswith(("/_summary.json", ".parquet")):
        return "Hawk imports scan summaries and parquet files under scans/"
    return None


def _upload(files: dict[str, Path], destination: str) -> None:
    """Upload artifacts, rejecting final object keys that trigger Hawk processors."""
    import fsspec  # type: ignore[import-untyped]

    fs, base = fsspec.core.url_to_fs(destination)
    targets = {relative: f"{base}/{relative}" for relative in files}
    if destination.startswith("s3://"):
        refused = [
            f"{relative}: {reason}"
            for relative, target in targets.items()
            if (reason := hawk_would_process(target.split("/", 1)[1]))
        ]
        if refused:
            raise ValueError("refusing to upload objects Hawk would act on: " + "; ".join(refused))
    for relative, file in files.items():
        target = targets[relative]
        fs.makedirs(target.rsplit("/", 1)[0], exist_ok=True)
        fs.put_file(str(file), target)


async def persist(root: Path, source: Path, name: str) -> str:
    """Upload a directory of agent-produced artifacts under this sample's prefix."""
    destination = _destination(root, name)
    files: dict[str, Path] = {}
    for file in source.rglob("*"):
        if file.is_symlink():
            raise ToolError("Artifact bundles cannot contain symlinks")
        if file.is_file():
            files[file.relative_to(source).as_posix()] = file
    last: Exception | None = None
    for attempt in range(3):
        try:
            await anyio.to_thread.run_sync(_upload, files, destination)
            return destination
        except Exception as ex:
            last = ex
            await anyio.sleep(2**attempt)
    raise ToolError(f"Could not save {name} to {destination}: {last}. Try again.")


@solver
def stage_inputs(root: Path) -> Solver:
    """Lay the staged inputs and the starting workspace into a fresh box, once.

    A resumed sample skips this: checkpointing restores /inputs and /workspace as
    the interrupted run left them.
    """

    async def solve(state: TaskState, generate: Generate) -> TaskState:
        settings_path = root / "artifacts.json"
        if settings_path.exists():
            settings = json.loads(settings_path.read_text())
            settings["sample_uuid"] = state.uuid
            settings_path.write_text(json.dumps(settings))
        marker = await sandbox().exec(["test", "-e", "/workspace/.staged"])
        if marker.success:
            return state
        stream = io.BytesIO()
        with tarfile.open(fileobj=stream, mode="w:gz", dereference=True) as archive:
            archive.add(root / "inputs", arcname="inputs")
            archive.add(root / "work", arcname="workspace")
        await write_into(stream.getvalue(), "/")
        await sandbox().exec(["touch", "/workspace/.staged"])
        return state

    return solve


INSTALL_RECORD = "/inputs/benchmark-install.json"


@solver
def install_benchmark(specs: list[str], source: str | None) -> Solver:
    """Install the benchmark into the auditor's box, as its jobs install it.

    The agent's shell is in the box; without this it could read the benchmark but not
    import its task, load its dataset, call its scorer or try a change without a paid
    job. The same pip specs as the jobs (the benchmark, its lockfile pins, the jobs'
    Inspect), so what the agent runs here is what its jobs run. A failed install is
    recorded for the agent, not fatal: the audit can still read and launch. Packages
    are not checkpointed, so a resumed box installs again unless `source` imports.
    """

    async def solve(state: TaskState, generate: Generate) -> TaskState:
        if source and (await sandbox().exec(["python", "-c", f"import {source}"])).success:
            return state
        result = await sandbox().exec(
            [
                "python",
                "-m",
                "pip",
                "install",
                "--no-cache-dir",
                "--disable-pip-version-check",
                *specs,
            ],
            timeout=1800,
        )
        imported = None
        if source and result.success:
            imported = (await sandbox().exec(["python", "-c", f"import {source}"])).success
        record = {
            "installed": result.success,
            "specs": specs,
            "source_module": source,
            "imports": imported,
            "log_tail": (result.stdout + result.stderr)[-4000:],
        }
        await sandbox().write_file(INSTALL_RECORD, json.dumps(record, indent=2))
        return state

    return solve


async def write_into(archive: bytes, destination: str) -> None:
    """Unpack a gzipped tar into the box at `destination`."""
    await sandbox().write_file("/tmp/staged.tar.gz", archive)
    result = await sandbox().exec(
        ["tar", "-xzf", "/tmp/staged.tar.gz", "-C", destination, "--no-same-owner"],
        timeout=600,
    )
    await sandbox().exec(["rm", "-f", "/tmp/staged.tar.gz"])
    if not result.success:
        raise ToolError(f"Could not stage files into the box: {result.stderr}")


async def copy_into_box(files: list[Path], destination: str) -> None:
    """Copy host files into one box directory, as one archive."""
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w:gz") as archive:
        for file in files:
            archive.add(file, arcname=file.name)
    await sandbox().exec(["mkdir", "-p", destination])
    await write_into(stream.getvalue(), destination)
