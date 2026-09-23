"""Transfer investigator files across the runner/sandbox boundary."""

import io
import json
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
        if initial:
            archive.add(root / "work", arcname="workspace")
    await sandbox().write_file("/tmp/investigator-inputs.tar.gz", stream.getvalue())
    result = await sandbox().exec([
        "python", "-c",
        "import tarfile; t=tarfile.open('/tmp/investigator-inputs.tar.gz'); "
        "t.extractall('/', filter='data')",
    ], timeout=300)
    if not result.success:
        raise ToolError(f"Could not stage investigator inputs: {result.stderr}")
    manifest_path.write_text(json.dumps(current))


def extract_workspace(data: bytes, destination: Path) -> None:
    """Reject links and special files before touching the trusted mirror."""
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as archive:
        for member in archive.getmembers():
            path = Path(member.name)
            if path.is_absolute() or ".." in path.parts or not (member.isfile() or member.isdir()):
                raise ValueError(f"Unsafe workspace archive member: {member.name}")
        archive.extractall(destination, filter="data")


async def pull(root: Path) -> None:
    if not remote_workspace(root):
        return
    result = await sandbox().exec([
        "tar", "--exclude=.git", "--exclude=.venv", "--exclude=__pycache__",
        "-czf", "/tmp/investigator-workspace.tar.gz", "-C", "/workspace", ".",
    ], timeout=300)
    if not result.success:
        raise ToolError(f"Could not collect investigator workspace: {result.stderr}")
    data = await sandbox().read_file("/tmp/investigator-workspace.tar.gz", text=False)
    with tempfile.TemporaryDirectory(dir=root) as temporary:
        staged = Path(temporary) / "work"
        staged.mkdir()
        extract_workspace(data, staged)
        shutil.rmtree(root / "work")
        shutil.move(str(staged), root / "work")


async def push_assessments(root: Path) -> None:
    if remote_workspace(root):
        path = root / "work/report/assessments.tex"
        if path.exists():
            await sandbox().write_file("/workspace/report/assessments.tex", path.read_bytes())


async def persist(root: Path, source: Path) -> str:
    """Upload agent-produced artifacts using the runner's scoped credentials."""
    import fsspec  # type: ignore[import-untyped]

    settings = json.loads((root / "remote-workspace.json").read_text())
    base = settings["artifact_dir"]
    destination = f"{base.rstrip('/')}/{settings['sample_uuid']}"

    def upload() -> None:
        fs, path = fsspec.core.url_to_fs(destination)
        for file in source.rglob("*"):
            if file.is_symlink():
                raise ValueError("Artifact bundles cannot contain symlinks")
            if file.is_file():
                target = f"{path}/{file.relative_to(source).as_posix()}"
                fs.makedirs(target.rsplit("/", 1)[0], exist_ok=True)
                fs.put_file(str(file), target)

    await anyio.to_thread.run_sync(upload)
    return destination


@solver
def stage_workspace(root: Path) -> Solver:
    async def solve(state: TaskState, generate: Generate) -> TaskState:
        path = root / "remote-workspace.json"
        settings = json.loads(path.read_text())
        settings["sample_uuid"] = state.uuid
        path.write_text(json.dumps(settings))
        await push(root, initial=True)
        result = await sandbox().exec(["latexmk", "-v"], timeout=60)
        if not result.success:
            raise RuntimeError("The investigator image cannot run latexmk")
        probe = root / "preflight"
        probe.mkdir(exist_ok=True)
        (probe / "preflight.json").write_text(json.dumps({
            "sandbox": "ready", "latex": "available", "sample_uuid": state.uuid,
        }))
        await persist(root, probe)
        return state
    return solve


def synchronized(tool: Tool, root: Path) -> Tool:
    """Keep trusted host tools independent of the sandbox provider."""
    definition = ToolDef(tool)

    async def execute(**kwargs: Any) -> Any:
        await pull(root)
        try:
            return await tool(**kwargs)
        finally:
            await push(root)

    return ToolDef(
        execute, name=definition.name, description=definition.description,
        parameters=definition.parameters, parallel=False,
    ).as_tool()
