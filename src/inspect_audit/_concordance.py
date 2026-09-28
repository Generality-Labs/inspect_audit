"""Prove the grade channel before the auditor uses it.

Each recorded attempt of the item is re-scored with the benchmark's own scorers,
through inspect's `score_async`, and the fresh grade is compared with the recorded
one. A disagreement is resampled to tell scorer noise (the regrades vary) from a
reconstruction fault (they do not). The verdict is stored on the sample: `grade`
refuses while it is `blocked`, and every item score carries it.
"""

import math
import re
from contextlib import nullcontext
from importlib.metadata import PackageNotFoundError, version
from logging import getLogger
from typing import Any

from inspect_ai import score_async
from inspect_ai.event import EventTreeNode, EventTreeSpan, ModelEvent, event_tree
from inspect_ai.log import EvalSample, EvalSpec, read_eval_log, transcript
from inspect_ai.model import get_model, model_roles
from inspect_ai.scorer import (
    CORRECT,
    INCORRECT,
    NOANSWER,
    PARTIAL,
    Score,
    Scorer,
    Target,
    Value,
    frequency,
    scorer,
)
from inspect_ai.scorer._scorer import unique_scorer_name
from inspect_ai.solver import Generate, Solver, TaskState, solver
from inspect_ai.util import StoreModel, registry_info, sandbox, sandbox_default
from pydantic import Field

from ._item import AUDIT_ROOT
from ._sandbox import BENCHMARK_SERVICE, has_benchmark_box
from ._state import replay_choices

logger = getLogger(__name__)

AGREE = "AGREE"
STABLE = "STABLE_DISAGREEMENT"
NOISY = "NOISY_DISAGREEMENT"
RESAMPLES = 3
NAME = "concordance"

UNSCORED = "UNSCORED"
# inspect's own categorical values compare the way its metrics read them; anything
# else a scorer returns is a label, and two different labels are two different grades
_STANDARD = {CORRECT: 1.0, INCORRECT: 0.0, PARTIAL: 0.5, NOANSWER: 0.0}


def _grade(value: Value | None) -> Any:
    """A grade in comparable form: 1, 1.0, True and "C" are one grade; "A" and "B" are two.

    `value_to_float` maps every string it does not know to 0.0, which made "A" and
    "B", or "correct" and "incorrect", compare equal. Unscored (None or NaN) is its
    own grade: NaN never equals itself, so it would otherwise read as noise.
    """
    if value is None:
        return UNSCORED
    if isinstance(value, bool):
        return float(value)
    if isinstance(value, (int, float)):
        return UNSCORED if math.isnan(value) else float(value)
    if isinstance(value, str):
        return _STANDARD.get(value, value)
    if isinstance(value, dict):
        return {str(k): _grade(v) for k, v in sorted(value.items())}
    return [_grade(v) for v in value]


def scorer_name(scorer: Scorer) -> str | None:
    """The name a log records this scorer's score under, when it has a registry name."""
    try:
        return registry_info(scorer).name.split("/")[-1]
    except Exception:  # an unregistered callable has no name to pair on
        return None


def paired(
    recorded: dict[str, Score], fresh: list[Score | None], names: list[str | None]
) -> list[tuple[int, Score, Score]]:
    """Recorded and fresh scores for the same scorer.

    By name: a log's score keys are the scorers' unqualified registry names
    (repeats numbered as inspect's unique_scorer_name does). Positionally only when
    a benchmark scorer has no name to pair on.
    """
    if all(names):
        seen: list[str] = []
        pairs = []
        for i, (name, score) in enumerate(zip(names, fresh, strict=True)):
            key = unique_scorer_name(str(name), seen)
            seen.append(key)
            if score is not None and key in recorded:
                pairs.append((i, recorded[key], score))
        return pairs
    ordered = list(recorded.values())
    return [
        (i, r, f) for i, (r, f) in enumerate(zip(ordered, fresh, strict=False)) if f is not None
    ]


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

    def under_scoring(nodes: list[EventTreeNode], inside: bool) -> set[str]:
        found: set[str] = set()
        for node in nodes:
            if isinstance(node, EventTreeSpan):
                found |= under_scoring(node.children, inside or node.type in ("scorers", "scorer"))
            elif inside and isinstance(node, ModelEvent):
                found.add(node.model)
        return found

    return under_scoring(event_tree(sample.events or []), False)


def grader_drift(logged: set[str], resolved: set[str]) -> dict[str, list[str]] | None:
    """The recorded scorers used models this replay will not; None when they match."""
    if not logged or {_family(m) for m in logged} <= {_family(m) for m in resolved}:
        return None
    return {"logged": sorted(logged), "resolved": sorted(resolved)}


@scorer(metrics=[frequency(categories=[AGREE, STABLE, NOISY])], name=NAME)
def concordance_scorer(benchmark: list[Scorer]) -> Scorer:
    """AGREE when the benchmark's scorers reproduce this attempt's recorded grades.

    Runs under `score_async(action="append")`, where `state.scores` holds the
    recorded grades keyed by scorer name; they are paired with the benchmark's
    scorers by that name.
    """
    names = [scorer_name(s) for s in benchmark]

    async def regrade(state: TaskState, target: Target) -> list[Score | None]:
        # a log keeps choices as strings: restore which one the answer picked, or
        # choice() grades every recorded multiple-choice answer wrong
        replay_choices(state)
        redirect = sandbox_default(BENCHMARK_SERVICE) if has_benchmark_box() else nullcontext()
        with redirect:
            return [await s(state, target) for s in benchmark]

    async def score(state: TaskState, target: Target) -> Score | None:
        recorded = dict(state.scores or {})
        fresh = await regrade(state, target)
        pairs = paired(recorded, fresh, names)
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
            if any(
                a is None or f is None or _grade(a.value) != _grade(f.value)
                for a, f in ((again[i], fresh[i]) for i in disagree)
            ):
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
        return (
            ("inconclusive", [*reasons, "stable_disagreement_box"])
            if has_box
            else ("blocked", [*reasons, "stable_disagreement"])
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
                        log,
                        [concordance_scorer(benchmark)],
                        action="append",
                        model=get_model(),
                        model_roles=dict(model_roles()),
                        display="plain",
                    )
                except Exception as ex:
                    errors.append(f"rescore_failed:{type(ex).__name__}")
                    continue
                fresh = [
                    (s.scores or {})[NAME] for s in scored.samples or [] if NAME in (s.scores or {})
                ]
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
            await sandbox().write_file(
                f"{AUDIT_ROOT}/concordance.json", con.model_dump_json(indent=1)
            )
        except Exception as ex:  # the store is the record; the file is for the auditor to read
            logger.warning("could not write concordance.json into the audit box: %s", ex)
        return state

    return solve
