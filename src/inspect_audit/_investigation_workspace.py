"""The investigator's box and the host's trusted copy of what is in it.

The box is the agent's only workspace, on Docker locally and on Kubernetes on Hawk
alike. The host keeps the inputs it staged and the logs its tools collected, and
reads the report back out of the box when the agent checks or publishes it: the
report is validated and published from the host, never trusted in place. Inspect's
checkpointing carries the box and the sample store across a crash.
"""

import io
import json
import logging
import posixpath
import shutil
import tarfile
import tempfile
from pathlib import Path

import anyio
import anyio.to_thread
import yaml
from inspect_ai.solver import Generate, Solver, TaskState, solver
from inspect_ai.tool import ToolError
from inspect_ai.util import sandbox

from .containers import EGRESS_POLICY

logger = logging.getLogger(__name__)

# The host reads only these parts of the agent's workspace: the report it validates
# and publishes, and the journal. Everything else the agent makes in /workspace
# (clones, venvs, downloads) stays in the box, so its links and sizes cannot break
# the sync. Job configs are fetched one file at a time by hawk_submit.
MIRRORED = ("report", "journal.md")


def max_pull_bytes() -> int:
    """What the sandbox will read back in one go, with headroom for the archive.

    Inspect's own read limit (`SandboxEnvironmentLimits`), which Hawk raises to 1 GiB
    through INSPECT_SANDBOX_MAX_READ_FILE_SIZE; a fixed number refused pulls there that
    would have transferred fine.
    """
    from inspect_ai.util import SandboxEnvironmentLimits

    return int(SandboxEnvironmentLimits.MAX_READ_FILE_SIZE * 0.9)


def configure(root: Path, image: str, artifact_dir: str) -> tuple[str, str]:
    (root / "artifacts.json").write_text(json.dumps({"artifact_dir": artifact_dir}))
    values = {
        "services": {
            "default": {
                "image": image,
                "command": ["sleep", "infinity"],
                "workingDir": "/workspace",
                "resources": {
                    "requests": {"cpu": "2", "memory": "8Gi"},
                    "limits": {"cpu": "4", "memory": "16Gi"},
                },
            }
        },
        "automountServiceAccountToken": False,
    }
    path = root / "investigator.values.yaml"
    path.write_text(yaml.safe_dump(values) + EGRESS_POLICY)
    return "k8s", str(path)


def extract_workspace(data: bytes, destination: Path) -> list[str]:
    """Extract regular files and directories only; return what was skipped.

    Links, devices and paths escaping the destination are never written to the
    trusted mirror. They are skipped rather than fatal: an agent's ordinary work
    (a venv, a cloned repository) makes them, and one must not end the run.
    """
    skipped: list[str] = []
    safe: list[tarfile.TarInfo] = []
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as archive:
        for member in archive.getmembers():
            path = Path(member.name)
            if path.is_absolute() or ".." in path.parts or not (member.isfile() or member.isdir()):
                skipped.append(member.name)
                continue
            safe.append(member)
        archive.extractall(destination, members=safe, filter="data")
    return skipped


async def pull(root: Path) -> list[str]:
    """Copy the report and journal to the host; return notes for the agent."""
    listing = await sandbox().exec(
        [
            "sh",
            "-c",
            'cd /workspace && for p in "$@"; do [ -e "$p" ] && echo "$p"; done; '
            "du -sb \"$@\" 2>/dev/null | awk '{s+=$1} END {print s+0}'",
            "sh",
            *MIRRORED,
        ],
        timeout=120,
    )
    if not listing.success:
        raise ToolError(f"Could not inspect the workspace: {listing.stderr}")
    lines = listing.stdout.split()
    present, size = lines[:-1], int(lines[-1]) if lines else 0
    if size > (limit := max_pull_bytes()):
        biggest = await sandbox().exec(
            ["sh", "-c", "cd /workspace && du -ab report | sort -rn | head -8"], timeout=120
        )
        raise ToolError(
            f"/workspace/report is {size // 2**20} MiB, over the {limit // 2**20} MiB "
            f"the host can read back. Move large files out of /workspace/report "
            f"(cite /inputs paths instead of copying logs):\n{biggest.stdout}"
        )
    if not present:
        return []
    # GNU tar exits 1 when a file changes while it is read; the archive is still whole
    result = await sandbox().exec(
        [
            "tar",
            "--exclude=.git",
            "--exclude=.venv",
            "--exclude=__pycache__",
            "--warning=no-file-changed",
            "--ignore-failed-read",
            "--hard-dereference",
            "-czf",
            "/tmp/investigator-workspace.tar.gz",
            "-C",
            "/workspace",
            *present,
        ],
        timeout=300,
    )
    if result.returncode not in (0, 1):
        raise ToolError(f"Could not collect investigator workspace: {result.stderr}")
    data = await sandbox().read_file("/tmp/investigator-workspace.tar.gz", text=False)
    work = root / "work"
    with tempfile.TemporaryDirectory(dir=root) as temporary:
        staged = Path(temporary)
        skipped = extract_workspace(data, staged)
        for name in MIRRORED:
            target = work / name
            if target.is_dir():
                shutil.rmtree(target)
            elif target.exists():
                target.unlink()
            if (staged / name).exists():
                shutil.move(str(staged / name), target)
    if skipped:
        shown = ", ".join(skipped[:10]) + (
            f" and {len(skipped) - 10} more" if len(skipped) > 10 else ""
        )
        return [f"not mirrored (links or special files): {shown}"]
    return []


async def fetch_inputs_files(root: Path, paths: list[str]) -> None:
    """Copy /inputs files the host does not have back from the box, on demand.

    A run resumed on a fresh runner has its collected job logs in the restored box but
    not in the host's scratch directory; publication validates cited evidence on the
    host, so it asks for exactly the files it cites.
    """
    for path in paths:
        normal = posixpath.normpath(path)
        if not normal.startswith("/inputs/"):
            continue
        host = root / "inputs" / Path(normal).relative_to("/inputs")
        if host.exists():
            continue
        try:
            content = await sandbox().read_file(normal, text=False)
        except FileNotFoundError:
            continue  # validation reports it as missing, with the path
        host.parent.mkdir(parents=True, exist_ok=True)
        host.write_bytes(content)


async def push_assessments(root: Path) -> None:
    path = root / "work/report/assessments.tex"
    if path.exists():
        await sandbox().write_file("/workspace/report/assessments.tex", path.read_bytes())


def _destination(root: Path, name: str) -> str:
    settings = json.loads((root / "artifacts.json").read_text())
    # keyed by sample and epoch, not by uuid: Inspect gives a resumed sample a new
    # uuid, and a resume must find (and add to) what the interrupted run saved
    key = settings["sample_key"]
    return f"{settings['artifact_dir'].rstrip('/')}/{key}/{name}"


def sample_key(state: TaskState) -> str:
    return f"{state.sample_id}-epoch{state.epoch}"


def hawk_would_process(key: str) -> str | None:
    """Why Hawk would act on an object created at this bucket key, else None.

    Hawk runs a processor on every object created under `evals/` and `scans/`
    (hawk 3.6.0: job_status_updated/index.py routes by prefix). Under `evals/`, any
    key ending `.eval` (bar `.fast.eval`) is imported into the shared warehouse,
    upserted by the eval id in its own header, so a copy of someone's log takes over
    their record (processors/eval.py process_object); a file directly in
    `evals/<eval set>/` is read as that eval set's own metadata, e.g. `.models.json`
    access groups. Under `scans/`, `_summary.json` and `.parquet` are imported as scan
    results (processors/scan.py). Only Hawk's own writers may create these.
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
    """Every upload this package makes goes through here, and none may trigger Hawk.

    2026-09-28: a published report carried copies of the logs it cited as evidence;
    they landed under evals/<run>/artifacts/ and Hawk re-pointed three shared
    warehouse records at them. The check is on the final object key, so no caller can
    route around it.
    """
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
        except Exception as ex:  # storage errors are transient more often than not
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
            # keyed by sample and epoch: a resumed sample gets a new uuid
            settings["sample_key"] = sample_key(state)
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
