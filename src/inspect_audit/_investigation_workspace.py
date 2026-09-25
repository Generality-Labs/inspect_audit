"""Transfer investigator files across the runner/sandbox boundary."""

import io
import json
import logging
import posixpath
import shutil
import tarfile
import tempfile
from pathlib import Path
from typing import Any

import anyio
import yaml
from inspect_ai.solver import Generate, Solver, TaskState, solver
from inspect_ai.tool import Tool, ToolDef, ToolError
from inspect_ai.util import sandbox

from .containers import EGRESS_POLICY

logger = logging.getLogger(__name__)

# The host reads only these parts of the agent's workspace: the report it validates
# and publishes, and the journal. Everything else the agent makes in /workspace
# (clones, venvs, downloads) stays in the box, so its links and sizes cannot break
# the sync. Job configs are fetched one file at a time by hawk_submit.
MIRRORED = ("report", "journal.md")
# Kubernetes read_file refuses more than 100 MiB; say what is large before it does.
MAX_PULL_BYTES = 90 * 1024 * 1024
# Host-side records that must outlive the runner pod.
STATE_FILES = ("jobs.json", "local_spend.json", "log_sources.json")


def remote_workspace(root: Path) -> bool:
    return (root / "remote-workspace.json").is_file()


def configure(root: Path, image: str, artifact_dir: str) -> tuple[str, str]:
    (root / "remote-workspace.json").write_text(json.dumps({"artifact_dir": artifact_dir}))
    values = {
        "services": {"default": {
            "image": image, "command": ["sleep", "infinity"],
            "workingDir": "/workspace",
            "resources": {"requests": {"cpu": "2", "memory": "8Gi"},
                          "limits": {"cpu": "4", "memory": "16Gi"}},
        }},
        "automountServiceAccountToken": False,
    }
    path = root / "investigator.values.yaml"
    path.write_text(yaml.safe_dump(values) + EGRESS_POLICY)
    return "k8s", str(path)


async def push(root: Path, *, initial: bool = False) -> None:
    """Send changed inputs; only initialize the agent's workspace once."""
    if not remote_workspace(root):
        return
    manifest_path = root / "transferred-inputs.json"
    old = json.loads(manifest_path.read_text()) if manifest_path.exists() else {}
    current = {}
    changed = False
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w:gz", dereference=True) as archive:
        for file in sorted((root / "inputs").rglob("*")):
            if not file.is_file():
                continue
            relative = file.relative_to(root).as_posix()
            stat = file.stat()
            current[relative] = [stat.st_size, stat.st_mtime_ns]
            if old.get(relative) != current[relative]:
                archive.add(file, arcname=relative)
                changed = True
        if initial:
            archive.add(root / "work", arcname="workspace")
            changed = True
    if not changed:
        return
    await sandbox().write_file("/tmp/investigator-inputs.tar.gz", stream.getvalue())
    result = await sandbox().exec([
        "python", "-c",
        "import tarfile; t=tarfile.open('/tmp/investigator-inputs.tar.gz'); "
        "t.extractall('/', filter='data')",
    ], timeout=300)
    if not result.success:
        raise ToolError(f"Could not stage investigator inputs: {result.stderr}")
    manifest_path.write_text(json.dumps(current))


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
    """Mirror the report and journal to the host; return notes for the agent."""
    if not remote_workspace(root):
        return []
    listing = await sandbox().exec(
        ["sh", "-c", "cd /workspace && for p in \"$@\"; do [ -e \"$p\" ] && echo \"$p\"; done; "
         "du -sb \"$@\" 2>/dev/null | awk '{s+=$1} END {print s+0}'", "sh", *MIRRORED],
        timeout=120,
    )
    if not listing.success:
        raise ToolError(f"Could not inspect the workspace: {listing.stderr}")
    lines = listing.stdout.split()
    present, size = lines[:-1], int(lines[-1]) if lines else 0
    if size > MAX_PULL_BYTES:
        biggest = await sandbox().exec(
            ["sh", "-c", "cd /workspace && du -ab report | sort -rn | head -8"], timeout=120
        )
        raise ToolError(
            f"/workspace/report is {size // 2**20} MiB, over the {MAX_PULL_BYTES // 2**20} MiB "
            f"the host can read back. Move large files out of /workspace/report "
            f"(cite /inputs paths instead of copying logs):\n{biggest.stdout}"
        )
    if not present:
        return []
    # GNU tar exits 1 when a file changes while it is read; the archive is still whole
    result = await sandbox().exec([
        "tar", "--exclude=.git", "--exclude=.venv", "--exclude=__pycache__",
        "--warning=no-file-changed", "--ignore-failed-read",
        "-czf", "/tmp/investigator-workspace.tar.gz", "-C", "/workspace", *present,
    ], timeout=300)
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
        shown = ", ".join(skipped[:10]) + (f" and {len(skipped) - 10} more" if len(skipped) > 10 else "")
        return [f"not mirrored (links or special files): {shown}"]
    return []


async def fetch_workspace_file(root: Path, path: str) -> Path:
    """Copy one file the agent wrote (e.g. a job config) to the host mirror."""
    normal = posixpath.normpath(path)
    if not normal.startswith("/workspace/"):
        raise ToolError("path must be under /workspace")
    host = root / "work" / Path(normal).relative_to("/workspace")
    if remote_workspace(root):
        try:
            content = await sandbox().read_file(normal, text=False)
        except FileNotFoundError as ex:
            raise ToolError(f"no such file: {path}") from ex
        host.parent.mkdir(parents=True, exist_ok=True)
        host.write_bytes(content)
    resolved = host.resolve()
    if not resolved.is_relative_to((root / "work").resolve()) or not resolved.is_file():
        raise ToolError(f"no such file: {path}")
    return resolved


async def push_assessments(root: Path) -> None:
    if remote_workspace(root):
        path = root / "work/report/assessments.tex"
        if path.exists():
            await sandbox().write_file("/workspace/report/assessments.tex", path.read_bytes())


def _destination(root: Path, name: str) -> str:
    settings = json.loads((root / "remote-workspace.json").read_text())
    return f"{settings['artifact_dir'].rstrip('/')}/{settings['sample_uuid']}/{name}"


def _upload(files: dict[str, Path], destination: str) -> None:
    import fsspec  # type: ignore[import-untyped]

    fs, base = fsspec.core.url_to_fs(destination)
    for relative, file in files.items():
        target = f"{base}/{relative}"
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


async def save_state(root: Path) -> None:
    """Keep the ledger, spend, journal and draft report outside the runner pod.

    Written after every host tool call and at cleanup, so a run that dies keeps its
    record of child jobs and spend and the report as far as it got.
    """
    if not remote_workspace(root):
        return
    manifest_path = root / "saved-state.json"
    old = json.loads(manifest_path.read_text()) if manifest_path.exists() else {}
    candidates: dict[str, Path] = {n: root / n for n in STATE_FILES if (root / n).is_file()}
    for base in (root / "jobs", root / "work"):
        if base.is_dir():
            for file in base.rglob("*"):
                if file.is_file() and not file.is_symlink():
                    candidates[file.relative_to(root).as_posix()] = file
    current = {k: [f.stat().st_size, f.stat().st_mtime_ns] for k, f in candidates.items()}
    changed = {k: f for k, f in candidates.items() if old.get(k) != current[k]}
    if not changed:
        return
    await anyio.to_thread.run_sync(_upload, changed, _destination(root, "state"))
    manifest_path.write_text(json.dumps(current))


@solver
def stage_workspace(root: Path) -> Solver:
    async def solve(state: TaskState, generate: Generate) -> TaskState:
        path = root / "remote-workspace.json"
        settings = json.loads(path.read_text())
        settings["sample_uuid"] = state.uuid
        path.write_text(json.dumps(settings))
        # a retried sample gets a fresh pod: everything must be sent again
        for name in ("transferred-inputs.json", "saved-state.json"):
            (root / name).unlink(missing_ok=True)
        await push(root, initial=True)
        result = await sandbox().exec(["latexmk", "-v"], timeout=60)
        if not result.success:
            raise RuntimeError("The investigator image cannot run latexmk")
        probe = root / "preflight"
        probe.mkdir(exist_ok=True)
        (probe / "preflight.json").write_text(json.dumps({
            "sandbox": "ready", "latex": "available", "sample_uuid": state.uuid,
        }))
        await persist(root, probe, "preflight")
        return state
    return solve


def cleanup(root: Path) -> Any:
    """Task cleanup: save what exists however the sample ended."""

    async def run(state: TaskState) -> None:
        if not remote_workspace(root):
            return
        try:
            await pull(root)
        except Exception as ex:
            logger.warning(f"Final workspace pull failed: {ex}")
        await save_state(root)

    return run


def synchronized(tool: Tool, root: Path) -> Tool:
    """Keep trusted host tools independent of the sandbox provider.

    Sync problems are reported alongside the tool's own result, never instead of
    it: a submission that went through must not look like one that failed.
    """
    definition = ToolDef(tool)

    async def execute(**kwargs: Any) -> Any:
        notes: list[str] = []
        try:
            notes += await pull(root)
        except Exception as ex:
            notes.append(f"workspace sync before this call failed: {ex}")
        try:
            result = await tool(**kwargs)
        finally:
            for step in (push, save_state):
                try:
                    await step(root)
                except Exception as ex:
                    notes.append(f"{step.__name__} after this call failed: {ex}")
                    logger.warning(f"{step.__name__} failed: {ex}")
        if notes and isinstance(result, str):
            result += "\n\n[workspace] " + "\n[workspace] ".join(notes)
        return result

    return ToolDef(
        execute, name=definition.name, description=definition.description,
        parameters=definition.parameters, parallel=False,
    ).as_tool()
