"""Validation of per-question labels recorded by the question-labels check."""

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
    # Empty on historical logs; new defect verdicts require a classification.
    defect_types: list[str] = Field(default_factory=list)
    scoring_effect: str | None = None
    next_check: str | None = None


def validate_labels(
    value: Any, expected: list[str], *, require_classification: bool = False
) -> list[QuestionAssessment]:
    """Require each expected question once; a clean verdict needs completed checks."""
    rows = TypeAdapter(list[QuestionAssessment]).validate_python(value)
    ids = [r.question_id for r in rows]
    if len(ids) != len(set(ids)) or set(ids) != set(expected):
        raise ValueError("question_assessments must contain every expected question exactly once")
    for row in rows:
        if row.status in ("NO_ISSUE_FOUND", "DEFECT") and (not row.checks or not row.evidence):
            raise ValueError(f"{row.question_id}: assessed labels need checks and evidence")
        if require_classification and row.status == "DEFECT" and not row.defect_types:
            raise ValueError(f"{row.question_id}: defects need at least one defect type")
        if row.defect_types and row.status != "DEFECT":
            raise ValueError(f"{row.question_id}: only established defects have defect types")
        if len(row.defect_types) != len(set(row.defect_types)):
            raise ValueError(f"{row.question_id}: duplicate defect types")
        if any(not s.strip() for s in row.checks + row.evidence + row.defect_types):
            raise ValueError(f"{row.question_id}: empty check or evidence reference")
    return rows
