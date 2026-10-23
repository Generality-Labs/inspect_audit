import pytest

from inspect_audit._labels import validate_labels


def label(qid, status):
    return {
        "question_id": qid,
        "status": status,
        "checks": ["compared specification and tests"],
        "evidence": ["source.py:12"],
        "explanation": "Observed evidence",
    }


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


@pytest.mark.parametrize("units", [None, ["part-a", "part-b"]])
def test_recorded_question_verdicts_retain_labels(tmp_path, units):
    import json

    from inspect_ai import Task, eval
    from inspect_ai.dataset import Sample
    from inspect_ai.solver import solver
    from inspect_ai.tool import ToolError
    from inspect_ai.util import store_as

    from inspect_audit._verdicts import (
        AssessmentUnits,
        Evidence,
        investigation_items,
        item_scorer,
        record_verdict,
    )

    expected = units if units is not None else ["1"]
    item = investigation_items(["question-labels"])[0]
    record = record_verdict([item])

    @solver
    def review():
        async def solve(state, generate):
            store_as(AssessmentUnits).ids = expected  # what the investigator sets at sample start
            args = {
                "item": "question-labels",
                "grade": "NO_ISSUE_FOUND",
                "explanation": "each question was checked against its source",
                "approaches": "reviewed",
                "tried": "checked",
                "remarks": "",
                "evidence": [Evidence(observed="check", source="source.py:12")],
            }
            with pytest.raises(ToolError, match="every expected question"):
                await record(**args, details=json.dumps({"question_assessments": []}))
            with pytest.raises(ToolError, match="defect type"):
                await record(
                    **{**args, "grade": "DEFECT"},
                    details=json.dumps(
                        {"question_assessments": [label(qid, "DEFECT") for qid in expected]}
                    ),
                )
            await record(
                **args,
                details=json.dumps(
                    {"question_assessments": [label(qid, "NO_ISSUE_FOUND") for qid in expected]}
                ),
            )
            return state

        return solve

    metadata = {"benchmark_metadata": {"arbitrary_structure": ["not", "assessment", "ids"]}}
    task = Task(
        dataset=[Sample(id="1", input="review", metadata=metadata)],
        solver=review(),
        scorer=item_scorer(item),
    )
    log = eval(task, model="mockllm/model", log_dir=str(tmp_path / "logs"), display="none")[0]
    assert log.status == "success", log.error
    [sample] = log.samples
    rows = sample.scores["question-labels"].metadata["question_assessments"]
    assert [row.question_id for row in validate_labels(rows, expected)] == expected


def test_defect_classification_is_required_for_new_verdicts():
    with pytest.raises(ValueError, match="defect type"):
        validate_labels([label("old", "DEFECT")], ["old"], require_classification=True)
    assert validate_labels([label("old", "DEFECT")], ["old"])
    custom = {**label("new", "DEFECT"), "defect_types": ["novel_mechanism"]}
    assert validate_labels([custom], ["new"], require_classification=True)
