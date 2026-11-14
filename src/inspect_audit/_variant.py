"""Modified copies of the investigated benchmark: the auditor's ablations (FRICTION 73).

The auditor changes the benchmark by writing replacement files: a folder whose files
sit at their paths inside the installed package (``inspect_evals/simpleqa/simpleqa.py``).
`overlay` reads them out of its box and checks them against the installed package;
the job carries them as data, beside the pinned benchmark and our pinned package, so
Hawk's code inventory still names exactly the benchmark's repository and every run
records the files that changed. `variant_task` builds the benchmark's task from a copy
of the installed package with those files laid over it.
"""

import difflib
import importlib
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

from inspect_ai import Task
from inspect_ai.util import registry_info

from .dispatch.jobs import VARIANT_MAX_BYTES as MAX_BYTES
from .dispatch.jobs import VARIANT_MAX_FILES as MAX_FILES


def task_package(task: str, source: str) -> tuple[str, Path]:
    """(the @task function's name in `source`, the installed top-level package's folder).

    `source` is the declared module that defines the task (the policy's
    `benchmark.source`); Inspect registers a task as `<package>/<function name>`.
    """
    function = task.split("/")[-1]
    try:
        found = getattr(importlib.import_module(source), function)
        registered = registry_info(found).name
    except Exception as ex:
        raise ValueError(f"{source}.{function} is not the @task {task!r}: {ex}") from ex
    # an installed extension package registers `<package>/<name>`; other modules, `<name>`
    if registered.split("/")[-1] != function:
        raise ValueError(f"{source}.{function} registers as {registered!r}, not {task!r}")
    from ._source import installed

    return function, installed(source)[1]


def check_overlay(task: str, source: str, files: dict[str, str]) -> str:
    """Refuse an overlay that is not a small change inside the benchmark's own package.

    Returns a unified diff against the installed files, for the job record.
    """
    _, root = task_package(task, source)
    top = root.name
    if not files:
        raise ValueError("the variant folder holds no files: put the files you changed in it")
    if len(files) > MAX_FILES:
        raise ValueError(f"a variant changes at most {MAX_FILES} files, not {len(files)}")
    size = sum(len(text.encode()) for text in files.values())
    if size > MAX_BYTES:
        raise ValueError(
            f"a variant is at most {MAX_BYTES // 1024} KB of files, not {size // 1024} KB"
        )
    diff: list[str] = []
    for name, text in sorted(files.items()):
        parts = Path(name).parts
        if not parts or parts[0] != top or ".." in parts or Path(name).is_absolute():
            raise ValueError(
                f"{name!r}: files sit at their path inside the installed package, starting {top}/"
            )
        installed = root.parent / name
        if not installed.parent.is_dir():
            raise ValueError(f"{name!r}: no such folder in the installed {top} package")
        before = installed.read_text().splitlines(keepends=True) if installed.is_file() else []
        diff += difflib.unified_diff(
            before, text.splitlines(keepends=True), f"a/{name}", f"b/{name}"
        )
    if not diff:
        raise ValueError("the variant's files are identical to the installed package")
    return "".join(diff)


def _laid_over(root: Path, files: dict[str, str]) -> Path:
    """A copy of the package at `root` with `files` laid over it: links to everything else."""
    base = Path(tempfile.mkdtemp(prefix="inspect-audit-variant-"))
    copy = base / root.name
    for folder, _, names in os.walk(root):
        here = copy / Path(folder).relative_to(root)
        here.mkdir(parents=True, exist_ok=True)
        for name in names:
            (here / name).symlink_to(Path(folder) / name)
    for name, text in files.items():
        target = base / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.unlink(missing_ok=True)
        target.write_text(text)
    return base


def variant_task(task: str, source: str, task_args: dict[str, Any], files: dict[str, str]) -> Task:
    """The benchmark's task as built by a copy of its package with `files` laid over it.

    The package's modules are dropped from `sys.modules` and imported again from the
    copy, so every module of the package, not only the task's, sees the change.
    """
    check_overlay(task, source, files)
    function, root = task_package(task, source)
    base = _laid_over(root, files)
    top = root.name
    for name in [m for m in sys.modules if m == top or m.startswith(top + ".")]:
        del sys.modules[name]
    sys.path.insert(0, str(base))
    importlib.invalidate_caches()
    loaded = importlib.import_module(source)
    if not str(getattr(loaded, "__file__", "")).startswith(str(base)):
        raise RuntimeError(f"{source} did not load from the variant copy")
    return getattr(loaded, function)(**task_args)
