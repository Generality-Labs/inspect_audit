"""Resolving a task spec, or a log, to the Inspect `Task` being audited.

An audit reads three things off the resolved task: its `dataset` (the samples and
their golds), its `scorer` (how grading actually works), and its `sandbox` (the
environment a sample runs in).
"""

from pathlib import Path
from typing import Any

from inspect_ai import Task

# Private core imports. Established practice in the ecosystem (Scout imports from
# `inspect_ai._cli.util` and `inspect_ai._util.*`), but it is our coupling point
# with core: if task loading moves, it moves here.
from inspect_ai._eval.loader import load_tasks
from inspect_ai.log import EvalLog, read_eval_log

__all__ = ["resolve_task", "resolve_task_from_log", "task_ref"]


def resolve_task(spec: str | Task, task_args: dict[str, Any] | None = None) -> Task:
    """Resolve a task spec to a `Task`.

    Accepts what `inspect eval` accepts: a registry name
    (`inspect_evals/simpleqa_verified`), a file (`./my_eval.py@my_task`), a
    directory, or an already-constructed `Task`.

    Args:
        spec: Task spec, or a `Task` to pass through.
        task_args: Task arguments (`-T`). These matter for correctness, not just
            configuration: arguments that alter sample inputs (a prompt suffix,
            say) change the dataset, so a task resolved with the wrong arguments
            will not match the logs. Prefer `resolve_task_from_log`.

    Returns:
        The resolved task.
    """
    if isinstance(spec, Task):
        return spec

    tasks = load_tasks([spec], task_args or {})
    if len(tasks) != 1:
        raise ValueError(
            f"Task spec {spec!r} resolved to {len(tasks)} tasks; an audit needs exactly "
            f"one. Address a single task, e.g. 'file.py@task_name'."
        )
    return tasks[0]


def resolve_task_from_log(log: str | Path | EvalLog) -> Task:
    """Resolve the task that produced a log, with the arguments it was run with.

    This is the safe way to resolve: the log records `task_registry_name`,
    `task_file` and `task_args`, so the dataset we reconstruct is the one the
    attempts were actually made against.

    Args:
        log: Log file path, or an already-read `EvalLog`.

    Returns:
        The resolved task.
    """
    header = log if isinstance(log, EvalLog) else read_eval_log(str(log), header_only=True)
    spec = header.eval
    args = spec.task_args or {}

    # Prefer the file the eval was actually run from, but a log routinely outlives
    # the file's location, so fall back to the registry name rather than failing.
    candidates: list[str] = []
    if spec.task_file and spec.task_registry_name:
        candidates.append(f"{spec.task_file}@{spec.task_registry_name}")
    if spec.task_registry_name:
        candidates.append(spec.task_registry_name)
    if spec.task:
        candidates.append(spec.task)

    errors: list[str] = []
    for candidate in candidates:
        try:
            return resolve_task(candidate, args)
        except Exception as ex:  # noqa: BLE001 - report every attempt, not the last
            errors.append(f"  {candidate!r}: {type(ex).__name__}: {ex}")

    raise ValueError(
        "Could not resolve the task that produced this log. Tried:\n"
        + "\n".join(errors)
        + "\nPass the task explicitly, or run from the directory the eval was run from."
    )


def task_ref(task: Task) -> str:
    """The string that addresses this task again later, for the audit's provenance."""
    return task.name
