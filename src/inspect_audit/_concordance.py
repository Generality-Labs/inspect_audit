"""Prove the grade channel before the auditor uses it.

Each recorded attempt of the item is re-scored with the benchmark's own scorers,
through inspect's `score_async`, and the fresh grade is compared with the recorded
one. A disagreement is resampled to tell scorer noise (the regrades vary) from a
reconstruction fault (they do not). The verdict is stored on the sample: `grade`
refuses while it is `blocked`, and every item score carries it.
"""

import re
from contextlib import nullcontext
from importlib.metadata import PackageNotFoundError, version
from logging import getLogger
from typing import Any

from inspect_ai import score_async
from inspect_ai.event import ModelEvent, SpanBeginEvent, SpanEndEvent
from inspect_ai.log import EvalSample, EvalSpec, read_eval_log, transcript
from inspect_ai.model import get_model, model_roles
from inspect_ai.scorer import (
    Score,
    Scorer,
    Target,
    Value,
    frequency,
    scorer,
    value_to_float,
)
from inspect_ai.solver import Generate, Solver, TaskState, solver
from inspect_ai.util import StoreModel, sandbox, sandbox_default
from pydantic import Field

from ._item import AUDIT_ROOT
from ._sandbox import BENCHMARK_SERVICE, has_benchmark_box

logger = getLogger(__name__)

AGREE = "AGREE"
STABLE = "STABLE_DISAGREEMENT"
NOISY = "NOISY_DISAGREEMENT"
RESAMPLES = 3
NAME = "concordance"

_to_float = value_to_float()


def _grade(value: Value) -> Any:
    """A grade as inspect's metrics see it, so 1, 1.0, True and "C" compare equal."""
    if isinstance(value, dict):
        return {str(k): _grade(v) for k, v in sorted(value.items())}
    if isinstance(value, list):
        return [_grade(v) for v in value]
    return _to_float(value)


class Concordance(StoreModel):
    """Whether this item's recorded grades are reproduced by the benchmark's scorers here.

    `verdict`: `validated`, `blocked` (stable disagreement, no box to explain it),
    `inconclusive` (stable disagreement a benchmark box could explain), or
    `unvalidated` (nothing checked). `reasons` are short codes.
    """

    verdict: str = "unvalidated"
    reasons: list[str] = Field(default_factory=list)
    attempted: int = 0
    checked: int = 0
    agreed: int = 0
    stable: list[dict[str, Any]] = Field(default_factory=list)
    noisy: list[dict[str, Any]] = Field(default_factory=list)
    drift: dict[str, Any] = Field(default_factory=dict)


def drift(header: EvalSpec, task_args: dict[str, Any]) -> dict[str, Any]:
    """Package and argument differences between the log and our resolution."""
    packages: dict[str, dict[str, str | None]] = {}
    for package, recorded in (header.packages or {}).items():
        try:
            installed: str | None = version(package)
        except PackageNotFoundError:
            installed = None
        if installed != recorded:
            packages[package] = {"logged": recorded, "resolved": installed}
    recorded_args = header.task_args or {}
    args = {
        key: {"logged": recorded_args.get(key), "resolved": task_args.get(key)}
        for key in sorted(set(recorded_args) | set(task_args))
        if recorded_args.get(key) != task_args.get(key)
    }
    return {"packages": packages, "args": args}


def _family(model: str) -> str:
    """`openai/gpt-5-mini-2025-08-07` and `openrouter/openai/gpt-5-mini` are one model."""
    return re.sub(r"-\d{4}-?\d{2}-?\d{2}$", "", model.rsplit("/", 1)[-1]).lower()


def scorer_models(sample: EvalSample) -> set[str]:
    """Models the recorded scorers called for this sample (an LLM extractor or judge)."""
    models: set[str] = set()
    scoring: list[str] = []
    for event in sample.events or []:
        if isinstance(event, SpanBeginEvent):
            if event.type in ("scorers", "scorer") or scoring:
                scoring.append(event.id)
        elif isinstance(event, SpanEndEvent):
            if scoring and event.id == scoring[-1]:
                scoring.pop()
        elif isinstance(event, ModelEvent) and scoring:
            models.add(event.model)
    return models


def grader_drift(logged: set[str], resolved: set[str]) -> dict[str, list[str]] | None:
    """The recorded scorers used models this replay will not; None when they match."""
    if not logged or {_family(m) for m in logged} <= {_family(m) for m in resolved}:
        return None
    return {"logged": sorted(logged), "resolved": sorted(resolved)}


@scorer(metrics=[frequency(categories=[AGREE, STABLE, NOISY])], name=NAME)
def concordance_scorer(benchmark: list[Scorer]) -> Scorer:
    """AGREE when the benchmark's scorers reproduce this attempt's recorded grades.

    Runs under `score_async(action="append")`, where `state.scores` holds the
    recorded grades in the order the log wrote them; they are paired with the
    benchmark's scorers positionally.
    """

    async def regrade(state: TaskState, target: Target) -> list[Score | None]:
        redirect = sandbox_default(BENCHMARK_SERVICE) if has_benchmark_box() else nullcontext()
        with redirect:
            return [await s(state, target) for s in benchmark]

    async def score(state: TaskState, target: Target) -> Score | None:
        recorded = list((state.scores or {}).values())[: len(benchmark)]
        fresh = await regrade(state, target)
        pairs = [(i, r, f) for i, (r, f) in enumerate(zip(recorded, fresh, strict=False)) if f is not None]
        if not pairs:
            return None
        mismatch = len(recorded) != len(benchmark)
        disagree = [i for i, r, f in pairs if _grade(r.value) != _grade(f.value)]
        metadata: dict[str, Any] = {
            "recorded": [_grade(r.value) for _, r, _ in pairs],
            "regraded": [_grade(f.value) for _, _, f in pairs],
            "scorer_count_mismatch": mismatch,
        }
        if not disagree:
            return Score(value=AGREE, metadata=metadata)
        # resamples identical to the first regrade are a fault; varying ones are noise.
        # a resample that fails proves nothing, so it counts as stable, never benign
        stable = True
        for _ in range(RESAMPLES):
            try:
                again = await regrade(state, target)
            except Exception:
                continue
            if any(a is None or f is None or _grade(a.value) != _grade(f.value) for a, f in ((again[i], fresh[i]) for i in disagree)):
                stable = False
                break
        return Score(value=STABLE if stable else NOISY, metadata=metadata)

    return score


def classify(
    scores: list[Score], *, attempted: int, has_box: bool, errors: list[str]
) -> tuple[str, list[str]]:
    """The verdict from per-attempt concordance scores."""
    reasons = list(errors)
    if attempted and not scores:
        return "unvalidated", [*reasons, "checked_none"]
    if not scores:
        return "unvalidated", reasons or ["no_attempts"]
    if any((s.metadata or {}).get("scorer_count_mismatch") for s in scores):
        return "unvalidated", [*reasons, "scorer_count_mismatch"]
    if any(s.value == NOISY for s in scores):
        reasons.append("noise")
    stable = [s for s in scores if s.value == STABLE]
    if stable:
        # a different grader model can disagree with the recorded grade without our
        # reconstruction being wrong, so that alone never closes the grade channel
        if all((s.metadata or {}).get("grader_drift") for s in stable):
            return "inconclusive", [*reasons, "grader_model_drift"]
        return ("inconclusive", [*reasons, "stable_disagreement_box"]) if has_box else (
            "blocked", [*reasons, "stable_disagreement"]
        )
    if len(scores) < attempted:
        reasons.append("partial_coverage")
    return "validated", reasons


@solver
def concordance_gate(scorers: Scorer | list[Scorer] | None, limit: int = 15) -> Solver:
    """Setup solver: re-score this item's recorded attempts before the auditor's first turn."""
    benchmark = [s for s in (scorers if isinstance(scorers, list) else [scorers]) if s is not None]

    async def solve(state: TaskState, generate: Generate) -> TaskState:
        con = state.store_as(Concordance)
        metadata = state.metadata or {}
        logs: list[str] = metadata.get("sliced_logs") or []
        task_args = (metadata.get("audit_item") or {}).get("task_args") or {}
        if not benchmark:
            con.reasons = ["no_scorer"]
        elif not logs:
            con.reasons = ["no_attempts"]
        else:
            scores: list[Score] = []
            errors: list[str] = []
            # what the replay can call: the audit's bound roles and its default model
            resolved = {str(m) for m in model_roles().values()} | {str(get_model())}
            for path in logs:
                try:
                    log = read_eval_log(path)
                except Exception as ex:
                    errors.append(f"unreadable_log:{type(ex).__name__}")
                    continue
                log.samples = (log.samples or [])[: max(0, limit - con.attempted)]
                if not log.samples:
                    continue
                con.attempted += len(log.samples)
                if not con.drift:
                    con.drift = drift(log.eval, task_args)
                logged = set().union(*(scorer_models(s) for s in log.samples))
                graders = grader_drift(logged, resolved)
                if graders:
                    con.drift.setdefault("grader_models", {})[path] = graders
                try:
                    scored = await score_async(
                        log, [concordance_scorer(benchmark)], action="append",
                        model=get_model(), model_roles=dict(model_roles()), display="plain",
                    )
                except Exception as ex:
                    errors.append(f"rescore_failed:{type(ex).__name__}")
                    continue
                fresh = [(s.scores or {})[NAME] for s in scored.samples or [] if NAME in (s.scores or {})]
                for item in fresh:
                    item.metadata = {**(item.metadata or {}), "grader_drift": graders}
                scores += fresh
            con.checked = len(scores)
            con.agreed = sum(1 for s in scores if s.value == AGREE)
            con.stable = [s.metadata or {} for s in scores if s.value == STABLE]
            con.noisy = [s.metadata or {} for s in scores if s.value == NOISY]
            if con.drift.get("packages") or con.drift.get("args") or con.drift.get("grader_models"):
                errors.append("resolution_drift")
            con.verdict, con.reasons = classify(
                scores, attempted=con.attempted, has_box=has_benchmark_box(), errors=errors
            )
        transcript().info({"concordance": con.model_dump()})
        try:
            await sandbox().write_file(f"{AUDIT_ROOT}/concordance.json", con.model_dump_json(indent=1))
        except Exception as ex:  # the store is the record; the file is for the auditor to read
            logger.warning("could not write concordance.json into the audit box: %s", ex)
        return state

    return solve
