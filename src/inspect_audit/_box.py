"""Putting things into a box: the one way to copy files in and to install packages.

The auditor's box and the investigator's box both get files and packages this way;
`service` names the box (None for the task's default).
"""

import io
import json
import tarfile
from pathlib import Path

from inspect_ai.tool import ToolError
from inspect_ai.util import sandbox


async def write_into(archive: bytes, destination: str, service: str | None = None) -> None:
    """Unpack a gzipped tar into the box at `destination`."""
    box = sandbox(service) if service else sandbox()
    staged = f"/tmp/staged-{abs(hash(destination))}.tar.gz"
    await box.write_file(staged, archive)
    await box.exec(["mkdir", "-p", destination])
    result = await box.exec(
        ["tar", "-xzf", staged, "-C", destination, "--no-same-owner"], timeout=600
    )
    await box.exec(["rm", "-f", staged])
    if not result.success:
        raise ToolError(f"Could not stage files into the box: {result.stderr}")


def archive(files: dict[str, Path]) -> bytes:
    """A gzipped tar of host files, each at its name (a path, or a whole directory)."""
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w:gz", dereference=True) as tar:
        for name, path in files.items():
            tar.add(path, arcname=name)
    return stream.getvalue()


async def copy_into_box(files: list[Path], destination: str, service: str | None = None) -> None:
    """Copy host files into one box directory."""
    await write_into(archive({f.name: f for f in files}), destination, service)


async def install(
    specs: list[str], record: str, service: str | None = None, source: str | None = None
) -> dict[str, object]:
    """Install pip specs into the box and write what happened to `record`; never fails.

    A failed install is recorded for the agent to read, not raised: it can still read the
    benchmark and work around it. With `source`, the record says whether that module
    then imports.
    """
    box = sandbox(service) if service else sandbox()
    result = await box.exec(
        ["python", "-m", "pip", "install", "--no-cache-dir", "--disable-pip-version-check", *specs],
        timeout=1800,
    )
    outcome: dict[str, object] = {"installed": result.success, "specs": specs}
    if source:
        outcome["source_module"] = source
        outcome["imports"] = (
            (await box.exec(["python", "-c", f"import {source}"])).success
            if result.success
            else None
        )
    outcome["log_tail"] = (result.stdout + result.stderr)[-4000:]
    await box.write_file(record, json.dumps(outcome, indent=2))
    return outcome
