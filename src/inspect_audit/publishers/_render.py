"""Run a renderer in the auditor's box, which carries TeX and Chromium.

The Inspect host may be anything (Hawk's runner has neither), but the box is our
image wherever the audit runs, so the PDF is drawn there and only bytes cross over.
"""

from pathlib import Path, PurePosixPath
from uuid import uuid4

from inspect_ai.tool import ToolError
from inspect_ai.util import sandbox


async def box_has(probe: str) -> bool:
    """Whether a shell probe succeeds in the box; False where there is no usable box."""
    try:
        result = await sandbox().exec(["sh", "-c", probe], timeout=60)
    except Exception:  # no sandbox in this context, or one that cannot exec
        return False
    return result.success


# the browser is installed in the box when first needed: the box has internet, and
# baking it into the image means a multi-GB build for one feature
PLAYWRIGHT = "1.64.1"
BROWSER = 'python -c "import playwright" && ls -d ~/.cache/ms-playwright/chromium* >/dev/null 2>&1'


async def ensure_browser() -> bool:
    """Whether the box can draw with Chromium, installing it there if needed.

    False where there is no box at all; a box that cannot install it is an error.
    """
    if await box_has(BROWSER):
        return True
    try:
        result = await sandbox().exec(
            [
                "sh",
                "-c",
                f"pip install -q playwright=={PLAYWRIGHT} && "
                "python -m playwright install --with-deps chromium",
            ],
            timeout=900,
        )
    except Exception:  # no sandbox in this context
        return False
    if not result.success:
        raise ToolError(f"Could not install Chromium in the box: {result.stderr[-2000:]}")
    return True


async def render_in_box(
    files: dict[str, bytes], command: list[str], outputs: str, timeout: int = 300
) -> dict[str, bytes]:
    """Write `files` under a fresh directory, run `command` there, return `outputs`' files.

    `command` and `outputs` are relative to that directory; `{dir}` in `command` is
    replaced by its path. Returns {name: bytes} for the regular files in `outputs`.
    """
    box = sandbox()
    work = PurePosixPath("/tmp") / f"render-{uuid4().hex}"
    await box.exec(["mkdir", "-p", str(work)])
    for name, data in files.items():
        await box.write_file(str(work / name), data)
    result = await box.exec(
        [part.replace("{dir}", str(work)) for part in command], cwd=str(work), timeout=timeout
    )
    if not result.success:
        raise ToolError(
            f"Rendering failed in the box: {result.stdout[-4000:]} {result.stderr[-2000:]}"
        )
    found = await box.exec(
        ["find", str(work / outputs), "-maxdepth", "1", "-type", "f", "-printf", "%f\n"]
    )
    if not found.success:
        raise ToolError(f"Rendering wrote no {outputs}: {found.stderr[-1000:]}")
    names = [n for n in found.stdout.splitlines() if n]
    back = {n: await box.read_file(str(work / outputs / n), text=False) for n in names}
    await box.exec(["rm", "-rf", str(work)])
    return back


def tree(directory: Path) -> dict[str, bytes]:
    """A directory's regular files as {relative path: bytes}."""
    return {
        str(p.relative_to(directory)): p.read_bytes()
        for p in sorted(directory.rglob("*"))
        if p.is_file()
    }
