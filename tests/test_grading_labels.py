"""The grading-labels check: an item judged on whether its recorded grades are right."""

import pytest


def test_grading_labels_carry_their_own_defect_types_and_a_headline() -> None:
    from inspect_audit._labels import GRADING_DEFECT_TYPES, validate_labels
    from inspect_audit._verdicts import investigation_items

    check = investigation_items(["grading-labels"])[0]
    assert check.flags == ["DEFECT"] and check.unresolved == ["UNRESOLVED"]
    assert "misgraded" in check.details and "grade" in check.tools
    row = {
        "question_id": "s0",
        "status": "DEFECT",
        "checks": ["re-graded the reference answer"],
        "evidence": ["/audit/logs/run.eval sample=s0 epoch=1"],
        "explanation": "The reference answer itself was graded incorrect.",
        "defect_types": ["false_negative"],
    }
    with pytest.raises(ValueError, match="needs a headline"):
        validate_labels([row], ["s0"], require_classification=True, allowed=GRADING_DEFECT_TYPES)
    row["headline"] = 'The grader rejects "Gul Shah" for the reference "Gul Shah".'
    validate_labels([row], ["s0"], require_classification=True, allowed=GRADING_DEFECT_TYPES)
    with pytest.raises(ValueError, match="unknown defect type"):
        validate_labels(
            [{**row, "defect_types": ["missing_information"]}],
            ["s0"],
            require_classification=True,
            allowed=GRADING_DEFECT_TYPES,
        )
    # the item vocabulary is unchanged for question-labels
    with pytest.raises(ValueError, match="unknown defect type"):
        validate_labels([{**row, "headline": None}], ["s0"], require_classification=True)
