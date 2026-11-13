"""What a benchmark repository says about itself: its task, the paths to snapshot, its paper.

Read from the repository's own files (eval.yaml, `@task` declarations, imports), so an
audit need not be told what the repository already states.
"""

import re
from logging import getLogger
from pathlib import Path

import yaml

logger = getLogger(__name__)


def only_task(source: Path) -> str | None:
    """The task, when the repository declares exactly one and nobody said which."""
    declared: set[str] = set()
    for path in sorted(source.rglob("*.py")):
        if any(part in {".git", "tests", "test", "build", ".venv"} for part in path.parts):
            continue
        try:
            text = path.read_text(errors="ignore")
        except OSError:
            continue
        for match in re.finditer(r"@task(?:\([^)]*\))?\s*\ndef\s+(\w+)", text):
            named = re.search(r'name\s*=\s*["\']([^"\']+)["\']', match.group(0))
            declared.add(named.group(1) if named else match.group(1))
    if len(declared) == 1:
        only = declared.pop()
        logger.info(f"investigating {only}: the only task {source} declares")
        return only
    return None


def paths_from_metadata(source: Path, target_task: str | None) -> list[str] | None:
    """Select the task directory, its local imports and package metadata."""
    from ._source import collect_source

    directory = _task_directory(source, target_task)
    if directory is None:
        return None
    # what the task's code imports, followed the way the investigator's source copy is
    base = source.resolve()
    chosen = {str(directory.relative_to(source))}
    starts = sorted(directory.rglob("*.py")) if directory.is_dir() else [directory]
    for start in starts:
        for file in collect_source(start.resolve()).files:
            if file.resolve().is_relative_to(base):
                chosen.add(str(file.resolve().relative_to(base)))
    for top in ("pyproject.toml", "README.md"):
        if (source / top).is_file():
            chosen.add(top)
    # a file inside a directory already named adds nothing to the snapshot
    directories = {c for c in chosen if (source / c).is_dir()}
    return sorted(c for c in chosen if not any(c.startswith(f"{d}/") for d in directories))


def _task_directory(source: Path, target_task: str | None) -> Path | None:
    """Find the task directory through eval.yaml or its @task declaration; return None if unknown."""
    if not target_task:
        return None
    name = target_task.split("/")[-1]
    for path in sorted(source.rglob("eval.yaml")):
        tasks = _safe_yaml(path).get("tasks")
        names = {
            str(entry.get("name", ""))
            for entry in (tasks if isinstance(tasks, list) else [])
            if isinstance(entry, dict)
        }
        if name in names or path.parent.name == name:
            return path.parent
    return _declaring_file(source, name)


# `@task` may name the task itself, or take the function's name. Both spellings, and
# the registry name may carry spaces where the function has underscores.
def _declaring_file(source: Path, name: str) -> Path | None:
    function = re.compile(
        rf"^\s*def {re.escape(name.replace(' ', '_').lower())}\s*\(", re.MULTILINE
    )
    declared = re.compile(rf"@task\([^)]*name\s*=\s*[\"']{re.escape(name)}[\"']", re.DOTALL)
    fallback: Path | None = None
    for path in sorted(source.rglob("*.py")):
        if any(part in {".git", "tests", "test", "build", ".venv"} for part in path.parts):
            continue
        try:
            text = path.read_text(errors="ignore")
        except OSError:
            continue
        if "@task" not in text:
            continue
        if declared.search(text):
            return path.parent
        if function.search(text) and fallback is None:
            fallback = path.parent
    return fallback


def _safe_yaml(path: Path) -> dict[str, object]:
    try:
        loaded = yaml.safe_load(path.read_text())
    except (yaml.YAMLError, OSError):
        return {}
    return loaded if isinstance(loaded, dict) else {}


def paper_from_metadata(source: Path, target_task: str | None) -> str | None:
    """The paper an eval names in its own metadata, when it names one."""
    if not target_task:
        return None
    directory = _task_directory(source, target_task)
    if directory is not None:
        arxiv = str(_safe_yaml(directory / "eval.yaml").get("arxiv") or "").strip()
        if arxiv:
            # several papers means the eval was revised; the last is the current one
            return arxiv.split(",")[-1].strip()
    return None
