"""The package's Inspect tasks, `investigation` and `audit`."""

import importlib
import importlib.util
from pathlib import Path
from typing import Any

from inspect_ai import Task, task
from inspect_ai.tool import WebSearchProviders

from ._audit import audit as audit
from ._log_sources import fetch_logs as fetch_logs
from ._log_sources import log_files as log_files
from ._prices import prices_for_models, register_prices
from ._publishing import publish as publish
from ._task import investigation_task
from .containers import INVESTIGATOR_IMAGE


def resolve_source(source: str | None) -> str | None:
    """`source=` as a path: a file or directory, or a module name resolved by Python."""
    if source is None or Path(source).exists():
        return source
    spec = importlib.util.find_spec(source)
    if spec is None or spec.origin is None:
        raise ValueError(f"source={source!r} is neither a path nor an importable module")
    return spec.origin


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
    search: WebSearchProviders | None = None,
    investigator_image: str | None = None,
    message_limit: int | None = None,
    time_limit: int | None = None,
    working_limit: int | None = None,
    model_prices: dict[str, dict[str, float]] | None = None,
) -> Task:
    """Investigate a benchmark task from inside its own samples (see `investigation_task` in `_task`).

    Everything is declared: `samples` is a list of ids or the literal "all"; `logs` are
    `.eval` files or `hawk:<eval set id>`; `source` is the task's file, a directory, or
    a module name. The investigator is the eval's own `--model`. With a `cost_limit`,
    prices come from Inspect, else OpenRouter's list; `model_prices` overrides them.
    `investigator_image` defaults to the published image; "build" builds it locally.
    """
    if isinstance(samples, str) and samples != "all":
        raise ValueError('samples must be a list of sample ids or the literal "all"')
    if model_prices:
        register_prices(model_prices)
    elif cost_limit is not None:
        # a cost limit needs a price for every model this sample can call: the
        # investigator (the run's --model) and the judges. Inspect's own costs first,
        # else OpenRouter's published price; a model with neither is refused there.
        from inspect_ai.model import get_model

        register_prices(prices_for_models([str(get_model()), *(judge or {}).values()]))
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
        search=search,
        # the published image unless told otherwise; "build" builds it here, from source
        investigator_image=None
        if investigator_image == "build"
        else investigator_image or INVESTIGATOR_IMAGE,
        cost_limit=cost_limit,
        message_limit=message_limit,
        time_limit=time_limit,
        working_limit=working_limit,
    )
