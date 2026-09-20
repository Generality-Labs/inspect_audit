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


def test_recorded_question_verdicts_round_trip_into_coverage(tmp_path):
    import json

    from inspect_ai import Task, eval
    from inspect_ai.dataset import Sample
    from inspect_ai.solver import solver
    from inspect_ai.tool import ToolError

    from inspect_audit._agent import Evidence, audit_items, item_scorer, record_verdict
    from inspect_audit._coverage import export_coverage

    item = audit_items(["question-labels"])[0]
    record = record_verdict([item])

    @solver
    def review():
        async def solve(state, generate):
            args = dict(item="question-labels", grade="NO_ISSUE_FOUND", approaches="reviewed",
                        tried="checked", remarks="", evidence=[Evidence(observed="check", source="source.py:12")])
            with pytest.raises(ToolError, match="every expected question"):
                await record(**args, details=json.dumps({"question_assessments": []}))
            # The author-provided step is excluded from the required labels.
            await record(**args, details=json.dumps({"question_assessments": [label("1.1", "NO_ISSUE_FOUND")]}))
            return state
        return solve

    task = Task(dataset=[Sample(id="1", input="review", metadata={"benchmark_metadata": {
        "sub_steps": [{"step_number": "1.1"}, {"step_number": "1.2", "provided_code": "return 1"}]}})],
        solver=review(), scorer=item_scorer(item))
    log = eval(task, model="mockllm/model", log_dir=str(tmp_path/'logs'), display="none")[0]
    assert log.status == "success", log.error
    result = export_coverage([log.location], ["1.1", "2.1"], tmp_path/'coverage.json')
    assert result['counts'] == dict(NO_ISSUE_FOUND=1, DEFECT=0, UNRESOLVED=0, NOT_ASSESSED=1)
    assert 'sample=1' in result['questions'][0]['assessments'][0]['evidence'][-1]
