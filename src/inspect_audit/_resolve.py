from pathlib import Path
from typing import Any

from inspect_ai import Task

# private core import, the same coupling scout takes on inspect_ai internals
from inspect_ai._eval.loader import load_tasks
from inspect_ai.log import EvalLog, read_eval_log


def resolve_task(spec: str | Task, task_args: dict[str, Any] | None = None) -> Task:
    """Resolve a task spec to a `Task`.

    Args:
        spec: What `inspect eval` accepts -- a registry name, `file.py@name`, a
            directory -- or a `Task` to pass through.
        task_args: Task arguments the task was run with.
    """
    if isinstance(spec, Task):
        return spec

    tasks = load_tasks([spec], task_args or {})
    if len(tasks) != 1:
        raise ValueError(
            f"Task spec {spec!r} resolved to {len(tasks)} tasks; an audit needs "
            "exactly one. Address a single task, e.g. 'file.py@task_name'."
        )
    return tasks[0]


def resolve_task_from_log(log: str | Path | EvalLog) -> Task:
    """Resolve the task that produced a log, with the arguments it was run with."""
    header = log if isinstance(log, EvalLog) else read_eval_log(str(log), header_only=True)
    spec = header.eval
    args = spec.task_args or {}

    # prefer the file the eval was run from, fall back to the registry name: a log
    # routinely outlives the file's location
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
        except Exception as ex:
            errors.append(f"  {candidate!r}: {type(ex).__name__}: {ex}")

    raise ValueError(
        "Could not resolve the task that produced this log. Tried:\n"
        + "\n".join(errors)
        + "\nPass the task explicitly, or run from the directory the eval was run from."
    )
