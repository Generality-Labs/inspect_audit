"""Validated question labels and deterministic audit coverage summaries."""

import json
from collections import Counter
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from .records import check_plain


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
    # one plain sentence for the page: what is wrong with the item, as its reader
    # would see it; the explanation above is the evidence, this is the headline
    headline: str | None = None
    # which audit item wrote the label: question-labels judges the item as written,
    # with no logs; grading-labels judges its recorded grades, from the logs. Absent
    # on labels written before the distinction existed, which were question-labels.
    check: str | None = None


# the audit items whose verdicts are question labels, and the check an untagged label
# belongs to
LABEL_CHECKS = ("question-labels", "grading-labels")
DEFAULT_CHECK = "question-labels"


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
        if require_classification and row.status == "DEFECT":
            if not (row.headline or "").strip():
                raise ValueError(
                    f"{row.question_id}: a defect needs a headline, one plain sentence saying "
                    "what is wrong with the item for the reader of the page"
                )
            check_plain(f"{row.question_id}: headline", row.headline or "")
        if row.defect_types and row.status != "DEFECT":
            raise ValueError(f"{row.question_id}: only established defects have defect types")
        if len(row.defect_types) != len(set(row.defect_types)):
            raise ValueError(f"{row.question_id}: duplicate defect types")
        if any(not s.strip() for s in row.checks + row.evidence + row.defect_types):
            raise ValueError(f"{row.question_id}: empty check or evidence reference")
    return rows


def _merge(states: set[str]) -> str:
    """One check's labels on one question: agreement stands, disagreement is unresolved."""
    return next(iter(states)) if len(states) == 1 else "UNRESOLVED" if states else "NOT_ASSESSED"


def _combine(by_check: dict[str, str]) -> str:
    """A question's status across checks.

    Defective if any check found a defect, otherwise open if any check is open,
    otherwise clean.

    The checks answer different questions (the item as written, its recorded grades),
    so a defect in either is a defect in the item, and a clean item is clean on both.
    """
    states = set(by_check.values())
    for status in ("DEFECT", "UNRESOLVED", "NOT_ASSESSED"):
        if status in states:
            return status
    return "NO_ISSUE_FOUND"


def coverage_summary(expected: list[str], assessments: list[dict[str, Any]]) -> dict[str, Any]:
    """Count each question once; disagreements remain unresolved, gaps unassessed.

    Labels are grouped by the check that wrote them. Within a check, two auditors who
    disagree leave the question unresolved. Across checks, a question is defective
    when any check found a defect and clean only when every check that ran on the
    sample found none; a check that ran on the sample but not on this question leaves
    it not assessed.
    """
    if not expected or len(expected) != len(set(expected)):
        raise ValueError("Expected population must be nonempty and unique")
    grouped: dict[str, dict[str, list[QuestionAssessment]]] = {}
    checks: list[str] = []
    for raw in assessments:
        row = QuestionAssessment.model_validate(raw)
        validate_labels([raw], [row.question_id])
        if row.question_id not in expected:
            raise ValueError(f"Question outside expected population: {row.question_id}")
        check = row.check or DEFAULT_CHECK
        if check not in checks:
            checks.append(check)
        grouped.setdefault(row.question_id, {}).setdefault(check, []).append(row)
    rows: list[dict[str, Any]] = []
    for qid in expected:
        labels = grouped.get(qid, {})
        by_check = {check: _merge({r.status for r in labels.get(check, [])}) for check in checks}
        rows.append(
            {
                "question_id": qid,
                "status": _combine(by_check) if by_check else "NOT_ASSESSED",
                "by_check": by_check,
                "assessments": [r.model_dump() for rows_ in labels.values() for r in rows_],
            }
        )
    counts = Counter(r["status"] for r in rows)
    categories = ["NO_ISSUE_FOUND", "DEFECT", "UNRESOLVED", "NOT_ASSESSED"]
    defects = Counter(
        kind
        for row in rows
        if row["status"] == "DEFECT"
        for kind in {kind for a in row["assessments"] for kind in a["defect_types"]}
    )
    return {
        "defect_counts": dict(sorted(defects.items())),
        "unclassified_defects": sum(
            row["status"] == "DEFECT" and not any(a["defect_types"] for a in row["assessments"])
            for row in rows
        ),
        "outstanding_ids": [
            row["question_id"] for row in rows if row["status"] in ("UNRESOLVED", "NOT_ASSESSED")
        ],
        "denominator": len(expected),
        "counts": {k: counts[k] for k in categories},
        "percentages": {k: 100 * counts[k] / len(expected) for k in categories},
        # each check's own counts, so a page can say how many defects are in the item
        # as written and how many in its grading; a question may count under both
        "counts_by_check": {
            check: {k: sum(r["by_check"].get(check) == k for r in rows) for k in categories}
            for check in checks
        },
        "questions": rows,
    }


def export_coverage(logs: list[str], expected: list[str], destination: Path) -> dict[str, Any]:
    """Extract the question labels from audit logs, retaining the source sample locator.

    Both label-writing audit items are read, question-labels (the item as written) and
    grading-labels (its recorded grades), each label tagged with the check that wrote
    it; pass the logs of every batch whose labels should count.
    """
    from inspect_ai.log import read_eval_log_samples

    rows: list[dict[str, Any]] = []
    for path in logs:
        for sample in read_eval_log_samples(path, all_samples_required=False):
            for check in LABEL_CHECKS:
                score = (sample.scores or {}).get(check)
                if score is None:
                    continue
                for raw in (score.metadata or {}).get("question_assessments", []):
                    validate_labels([raw], [raw["question_id"]])
                    row = dict(raw)
                    row["check"] = check
                    row["evidence"] = [
                        *row.get("evidence", []),
                        f"{path} sample={sample.id} epoch={sample.epoch}",
                    ]
                    rows.append(row)
    result = coverage_summary(expected, rows)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(result, indent=2))
    return result
