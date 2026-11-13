"""The package's Inspect tasks: `investigation`, `regrade` and `audit`."""

from pathlib import Path
from typing import Any

from inspect_ai import Task, task
from inspect_ai.tool import WebSearchProviders

from ._audit import audit as audit
from ._log_sources import fetch_logs as fetch_logs
from ._log_sources import log_files as log_files
from ._publishing import publish as publish
from ._task import investigation_task
from .containers import INVESTIGATOR_IMAGE


def resolve_source(source: str | None) -> str | None:
    """`source=` as a path: a file or directory, or a module name resolved by Python."""
    if source is None or Path(source).exists():
        return source
    from ._source import installed

    try:
        return str(installed(source)[0])
    except ValueError as ex:
        raise ValueError(f"source={source!r} is neither a path nor an importable module") from ex


@task
def investigation(
    task: str,
    samples: list[str] | str,
    checks: list[str],
    cost_limit: float | None = None,
    task_args: dict[str, Any] | None = None,
    logs: list[str] | None = None,
    judge: dict[str, str] | None = None,
    judge_configs: dict[str, dict[str, Any]] | None = None,
    source: str | None = None,
    compose: str | None = None,
    assessment_ids: dict[str, list[str]] | None = None,
    redact: list[str] | None = None,
    confidential: bool = False,
    notes: str | None = None,
    paper: str | None = None,
    search: WebSearchProviders | None = None,
    investigator_image: str | None = None,
    message_limit: int | None = None,
    time_limit: int | None = None,
    working_limit: int | None = None,
    install: list[str] | None = None,
) -> Task:
    """Investigate a benchmark task from inside its own samples (see `investigation_task` in `_task`).

    Everything is declared: `samples` is a list of ids or the literal "all"; `logs` are
    `.eval` files or `hawk:<eval set id>`; `source` is the task's file, a directory, or
    a module name. The investigator is the eval's own `--model`. Uncapped unless the
    operator sets `cost_limit`: the launching audit prices its jobs itself (`_spend`).
    `investigator_image` defaults to the published image; "build" builds it locally.
    """
    if isinstance(samples, str) and samples != "all":
        raise ValueError('samples must be a list of sample ids or the literal "all"')
    return investigation_task(
        task,
        samples=samples if samples == "all" else list(samples),
        checks=checks,
        task_args=task_args,
        logs=log_files(logs) if logs else None,
        judge=judge,
        judge_configs=judge_configs,
        source=resolve_source(source),
        compose=compose,
        assessment_ids=assessment_ids,
        redact=redact or (),
        confidential=confidential,
        notes=notes,
        paper=paper,
        search=search,
        install=install or (),
        # the published image unless told otherwise; "build" builds it here, from source
        investigator_image=None
        if investigator_image == "build"
        else investigator_image or INVESTIGATOR_IMAGE,
        cost_limit=cost_limit,
        message_limit=message_limit,
        time_limit=time_limit,
        working_limit=working_limit,
    )


@task
def regrade(
    task: str,
    logs: list[str],
    task_args: dict[str, Any] | None = None,
    judge: dict[str, str] | None = None,
    samples: list[str] | None = None,
    epochs: int = 3,
    cost_limit: float | None = None,
    files: dict[str, str] | None = None,
    source: str | None = None,
) -> Task:
    """Re-grade recorded attempts with the benchmark's own scorers (`regrade_task` in `_regrade`).

    The eval's `--model` is never called; on Hawk, make it the judge so the job gets
    the judge's credentials.
    """
    from ._regrade import regrade_task

    return regrade_task(
        task,
        logs=log_files(logs),
        task_args=task_args,
        judge=judge,
        samples=samples,
        epochs=epochs,
        cost_limit=cost_limit,
        files=files,
        source=source,
    )


@task
def variant(
    task: str, source: str, files: dict[str, str], task_args: dict[str, Any] | None = None
) -> Task:
    """The investigated benchmark with the auditor's replacement `files` laid over it.

    An ablation (`_variant`), recorded file by file in the job. `source` is the module
    that defines the task.
    """
    from ._variant import variant_task

    return variant_task(task, source, task_args or {}, files)
