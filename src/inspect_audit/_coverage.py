"""Validated question labels and deterministic audit coverage summaries."""

import json
from collections import Counter
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter


class QuestionAssessment(BaseModel):
    """One question's validity, independent of a model's recorded pass or failure."""

    model_config = ConfigDict(extra="forbid")
    question_id: str = Field(min_length=1)
    status: Literal["NO_ISSUE_FOUND", "DEFECT", "UNRESOLVED", "NOT_ASSESSED"]
    checks: list[str]
    evidence: list[str]
    explanation: str = Field(min_length=1)


def validate_labels(value: Any, expected: list[str]) -> list[QuestionAssessment]:
    """Require each expected question once; a clean verdict needs completed checks."""
    rows = TypeAdapter(list[QuestionAssessment]).validate_python(value)
    ids = [r.question_id for r in rows]
    if len(ids) != len(set(ids)) or set(ids) != set(expected):
        raise ValueError("question_assessments must contain every expected question exactly once")
    for row in rows:
        if row.status in ("NO_ISSUE_FOUND", "DEFECT") and (not row.checks or not row.evidence):
            raise ValueError(f"{row.question_id}: assessed labels need checks and evidence")
        if any(not s.strip() for s in row.checks + row.evidence):
            raise ValueError(f"{row.question_id}: empty check or evidence reference")
    return rows


def coverage_summary(expected: list[str], assessments: list[dict[str, Any]]) -> dict[str, Any]:
    """Count each question once; disagreements remain unresolved, gaps unassessed."""
    if not expected or len(expected) != len(set(expected)):
        raise ValueError("Expected population must be nonempty and unique")
    grouped: dict[str, list[QuestionAssessment]] = {}
    for raw in assessments:
        row = QuestionAssessment.model_validate(raw)
        validate_labels([raw], [row.question_id])
        if row.question_id not in expected:
            raise ValueError(f"Question outside expected population: {row.question_id}")
        grouped.setdefault(row.question_id, []).append(row)
    rows: list[dict[str, Any]] = []
    for qid in expected:
        labels = grouped.get(qid, [])
        states = {r.status for r in labels}
        status = next(iter(states)) if len(states) == 1 else "UNRESOLVED" if states else "NOT_ASSESSED"
        rows.append({"question_id": qid, "status": status, "assessments": [r.model_dump() for r in labels]})
    counts = Counter(r["status"] for r in rows)
    categories = ["NO_ISSUE_FOUND", "DEFECT", "UNRESOLVED", "NOT_ASSESSED"]
    return {"denominator": len(expected), "counts": {k: counts[k] for k in categories},
            "percentages": {k: 100 * counts[k] / len(expected) for k in categories}, "questions": rows}


def export_coverage(logs: list[str], expected: list[str], destination: Path) -> dict[str, Any]:
    """Extract question-labels scorer metadata, retaining the source sample locator."""
    from inspect_ai.log import read_eval_log_samples

    rows: list[dict[str, Any]] = []
    for path in logs:
        for sample in read_eval_log_samples(path, all_samples_required=False):
            score = (sample.scores or {}).get("question-labels")
            if score is None:
                continue
            for raw in (score.metadata or {}).get("question_assessments", []):
                validate_labels([raw], [raw["question_id"]])
                row = dict(raw)
                row["evidence"] = [*row.get("evidence", []), f"{path} sample={sample.id} epoch={sample.epoch}"]
                rows.append(row)
    result = coverage_summary(expected, rows)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(result, indent=2))
    return result
