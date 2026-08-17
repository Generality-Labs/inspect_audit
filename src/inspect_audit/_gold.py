"""Recovering the reference material for a sample.

Facts only: `target` verbatim, and any URLs the sample's metadata cites. What they
are worth — a cited source is circular confirmation, a rubric may be the real gold —
is skill content, not something this module asserts.

`target` is deliberately not treated as *the* gold. Some benchmarks keep reference
material elsewhere (a patch, a test file, an acceptable range, a list of
alternatives), so the sample's full metadata is preserved in `sample.json` and the
metadata keys are listed here for the auditor to interpret.
"""

import json
import re
from pathlib import Path
from typing import Any

from inspect_ai import Task
from inspect_ai.dataset import Sample
from inspect_ai.util import resource

__all__ = ["gold_facts", "grading_doc", "render_input"]

_TEMPLATE = Path(__file__).parent / "templates" / "grading.md"

_URL = re.compile(r"https?://[^\s,;'\"\]\)}]+")


def render_input(value: Any) -> str:
    """A sample input as readable text, whether a string or a message list."""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "\n\n".join(
            f"## {getattr(m, 'role', '?')}\n\n"
            f"{getattr(m, 'text', None) or getattr(m, 'content', '')}"
            for m in value
        )
    return str(value)


def gold_facts(sample: Sample) -> dict[str, Any]:
    """Reference material recovered for a sample."""
    target = sample.target if isinstance(sample.target, str) else list(sample.target)
    metadata = sample.metadata or {}
    return {
        "target": target,
        "cited_urls": sorted(set(_URL.findall(json.dumps(metadata, default=str)))),
        "metadata_keys": sorted(metadata.keys()),
    }


def grading_doc(task: Task, sample: Sample, criteria: dict[str, dict[str, Any]]) -> str:
    """Render `gold/grading.md` from the template and the recovered criteria.

    The prose lives in `templates/grading.md` so it is reviewable and editable
    without touching code; this only substitutes facts into it.
    """
    scorers = task.scorer if isinstance(task.scorer, list) else [task.scorer]
    fn = next((getattr(s, "__wrapped__", s) for s in scorers if s is not None), None)
    qualname = getattr(fn, "__qualname__", getattr(fn, "__name__", "?"))
    module = getattr(fn, "__module__", "?")

    def value(name: str) -> Any:
        entry = criteria.get(name, {})
        return entry.get("logged", entry.get("live"))

    judge_model = value("grader_model") or value("model")
    role = value("model_role")
    if judge_model or role:
        judge_line = (
            "**Graded by a model.** "
            + (f"Judge model: `{judge_model}`. " if judge_model else "")
            + (f"Model role: `{role}`. " if role else "")
            + "So grading is a generation, not a deterministic comparison: the same "
            "answer can be graded differently on different runs."
        )
    else:
        judge_line = "**Graded programmatically** — no judge model was recovered."

    score_map = value("score_map")
    values = (
        ", ".join(f"`{v}` ({k})" for k, v in score_map.items())
        if isinstance(score_map, dict)
        else "not recovered — read the source"
    )
    passing = ", ".join(f"`{v}`" for v in sorted(pass_values(criteria) or [])) or "not recovered"

    drifted = sorted(
        k for k, v in criteria.items() if "logged" in v and "live" in v and v["logged"] != v["live"]
    )
    drift_line = (
        "**The grader has changed since these attempts were run**, on: "
        + ", ".join(f"`{k}`" for k in drifted)
        + ". The attempts were graded by the logged configuration, not the current one."
        if drifted
        else ""
    )

    return resource(str(_TEMPLATE)).format(
        scorer=qualname,
        module=module,
        judge_line=judge_line,
        values=values,
        passing=passing,
        drift_line=drift_line,
        metadata_keys=(
            "\n".join(f"- `{k}`" for k in sorted((sample.metadata or {}).keys())) or "(none)"
        ),
    )


def pass_values(criteria: dict[str, dict[str, Any]]) -> set[str] | None:
    """Re-exported from `_elicitation` for template rendering."""
    from ._elicitation import pass_values as _pv

    return _pv(criteria)
