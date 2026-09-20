import pytest

from inspect_audit._coverage import coverage_summary, validate_labels


def label(qid, status):
    return dict(question_id=qid, status=status, checks=["compared specification and tests"],
                evidence=["source.py:12"], explanation="Observed evidence")


def test_missing_and_disputed_labels_never_become_clean():
    result = coverage_summary(["1.1", "1.2", "1.3"], [
        label("1.1", "NO_ISSUE_FOUND"), label("1.1", "NO_ISSUE_FOUND"),
        label("1.2", "DEFECT"), label("1.2", "NO_ISSUE_FOUND")])
    assert result["counts"] == dict(NO_ISSUE_FOUND=1, DEFECT=0, UNRESOLVED=1, NOT_ASSESSED=1)
    assert sum(result["counts"].values()) == result["denominator"] == 3


def test_complete_unique_ids_and_evidence_required():
    with pytest.raises(ValueError):
        validate_labels([label("1.1", "DEFECT")], ["1.1", "1.2"])
    with pytest.raises(ValueError):
        validate_labels([label("1.1", "DEFECT")] * 2, ["1.1"])
    row = label("1.1", "NO_ISSUE_FOUND")
    row["evidence"] = []
    with pytest.raises(ValueError):
        validate_labels([row], ["1.1"])
    row["status"] = "UNRESOLVED"
    assert validate_labels([row], ["1.1"])[0].status == "UNRESOLVED"
