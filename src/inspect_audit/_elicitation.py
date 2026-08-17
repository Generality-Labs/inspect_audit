"""Recovering how both sides were configured: the model under test, and the judge.

Facts only. What they mean, and what an auditor should do about them, is skill
content rather than something this module explains.

Agent side comes from `EvalPlan.steps` and `.config`, `EvalSpec.config`, `.model_args`
and `.task_args`. Judge side comes from `EvalSpec.scorers[].options` — what actually
graded these attempts — and from the live scorer's closure, which is what would grade
now. Nothing is keyed on a benchmark's own vocabulary.
"""

import json
from typing import Any

from inspect_ai import Task
from inspect_ai.log import EvalLog

__all__ = ["agent_configs", "judge_criteria", "pass_values"]

# Closure names worth recovering from a scorer, plus any long string: a rubric is
# usually the longest thing in there.
_CRITERIA_NAMES = frozenset(
    {
        "template",
        "grader_template",
        "instructions",
        "score_map",
        "grade_field",
        "model_role",
        "model",
        "rubric",
        "criteria",
        "partial_credit",
    }
)
_LONG_STRING = 200


def _set_fields(model: Any) -> dict[str, Any]:
    if model is None:
        return {}
    dumped = model.model_dump() if hasattr(model, "model_dump") else dict(model)
    return {k: v for k, v in dumped.items() if v not in (None, {}, [])}


def agent_configs(logs: list[EvalLog]) -> list[dict[str, Any]]:
    """Distinct agent configurations across `logs`, each with the models that used it.

    Grouped on what changes *behaviour* — the solver chain, generate config, eval
    config and arguments. Framework versions are reported per group but deliberately
    not part of the key: a field assembled over months carries a different
    `inspect_ai` version in almost every log, and grouping on that turns 49 logs into
    49 "distinct configurations" and hides the fact they were run identically.
    """
    groups: dict[str, dict[str, Any]] = {}
    for log in logs:
        plan = log.plan
        config = {
            "solver_chain": [
                {"solver": step.solver, "params": step.params or {}}
                for step in (getattr(plan, "steps", None) or [])
            ],
            "generate_config": _set_fields(getattr(plan, "config", None)),
            "eval_config": _set_fields(log.eval.config),
            "model_args": log.eval.model_args or {},
            "task_args": log.eval.task_args or {},
        }
        key = json.dumps(config, sort_keys=True, default=str)
        entry = groups.setdefault(key, {"config": config, "models": [], "packages": []})
        entry["models"].append(log.eval.model)
        entry["packages"].append(log.eval.packages or {})

    for entry in groups.values():
        entry["models"] = sorted(set(entry["models"]))
        entry["packages"] = sorted(
            {json.dumps(p, sort_keys=True) for p in entry["packages"]}
        )
    return sorted(groups.values(), key=lambda e: -len(e["models"]))


def judge_criteria(task: Task, logs: list[EvalLog]) -> dict[str, dict[str, Any]]:
    """Grading criteria, each labelled with where it was recovered from.

    `logged` is what graded these attempts; `live` is what would grade now. A key
    carrying both, with different values, means the grader changed since the run.
    """
    found: dict[str, dict[str, Any]] = {}

    for log in logs:
        for scorer in log.eval.scorers or []:
            for key, value in (scorer.options or {}).items():
                found.setdefault(key, {})["logged"] = value

    scorers = task.scorer if isinstance(task.scorer, list) else [task.scorer]
    for scorer_fn in scorers:
        if scorer_fn is None:
            continue
        fn = getattr(scorer_fn, "__wrapped__", scorer_fn)
        code, closure = getattr(fn, "__code__", None), getattr(fn, "__closure__", None)
        if code is None or not closure:
            continue
        for name, cell in zip(code.co_freevars, closure, strict=False):
            try:
                value = cell.cell_contents
            except ValueError:
                continue
            if not isinstance(value, (str, dict, int, float, bool)):
                continue
            if name in _CRITERIA_NAMES or (isinstance(value, str) and len(value) > _LONG_STRING):
                found.setdefault(name, {})["live"] = value

    return found


def pass_values(criteria: dict[str, dict[str, Any]]) -> set[str] | None:
    """Score values meaning "passed", from the scorer's own map rather than a guess."""
    entry = criteria.get("score_map", {})
    mapping = entry.get("logged") or entry.get("live")
    if not isinstance(mapping, dict):
        return None
    passes = {
        str(v) for k, v in mapping.items() if str(k).upper() in {"CORRECT", "C", "PASS", "TRUE"}
    }
    return passes or None
