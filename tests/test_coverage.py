import pytest

from inspect_audit._coverage import coverage_summary, validate_labels


def label(qid, status):
    return {
        "question_id": qid,
        "status": status,
        "checks": ["compared specification and tests"],
        "evidence": ["source.py:12"],
        "explanation": "Observed evidence",
    }


def test_missing_and_disputed_labels_never_become_clean():
    result = coverage_summary(
        ["1.1", "1.2", "1.3"],
        [
            label("1.1", "NO_ISSUE_FOUND"),
            label("1.1", "NO_ISSUE_FOUND"),
            label("1.2", "DEFECT"),
            label("1.2", "NO_ISSUE_FOUND"),
        ],
    )
    assert result["counts"] == {
        "NO_ISSUE_FOUND": 1,
        "DEFECT": 0,
        "UNRESOLVED": 1,
        "NOT_ASSESSED": 1,
    }
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


@pytest.mark.parametrize("units", [None, ["part-a", "part-b"]])
def test_recorded_question_verdicts_round_trip_into_coverage(tmp_path, units):
    import json

    from inspect_ai import Task, eval
    from inspect_ai.dataset import Sample
    from inspect_ai.solver import solver
    from inspect_ai.tool import ToolError

    from inspect_audit._agent import Evidence, audit_items, item_scorer, record_verdict
    from inspect_audit._coverage import export_coverage

    expected = units if units is not None else ["1"]
    item = audit_items(["question-labels"])[0]
    record = record_verdict([item])

    @solver
    def review():
        async def solve(state, generate):
            args = {
                "item": "question-labels",
                "grade": "NO_ISSUE_FOUND",
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
    if units is not None:
        metadata["assessment_ids"] = units
    task = Task(
        dataset=[Sample(id="1", input="review", metadata=metadata)],
        solver=review(),
        scorer=item_scorer(item),
    )
    log = eval(task, model="mockllm/model", log_dir=str(tmp_path / "logs"), display="none")[0]
    assert log.status == "success", log.error
    result = export_coverage([log.location], [*expected, "missing"], tmp_path / "coverage.json")
    assert result["counts"] == {
        "NO_ISSUE_FOUND": len(expected),
        "DEFECT": 0,
        "UNRESOLVED": 0,
        "NOT_ASSESSED": 1,
    }
    assert "sample=1" in result["questions"][0]["assessments"][0]["evidence"][-1]


def test_defect_counts_deduplicate_and_outstanding_units_survive():
    rows = [
        {
            **label("parent.a", "DEFECT"),
            "defect_types": ["missing_information", "incorrect_reference_or_test"],
        },
        {**label("parent.a", "DEFECT"), "defect_types": ["missing_information"]},
        label("parent.b", "UNRESOLVED"),
        label("other", "DEFECT"),
    ]
    result = coverage_summary(["parent.a", "parent.b", "other"], rows)
    assert result["defect_counts"] == {"incorrect_reference_or_test": 1, "missing_information": 1}
    assert result["unclassified_defects"] == 1
    assert result["outstanding_ids"] == ["parent.b"]
    with pytest.raises(ValueError, match="defect type"):
        validate_labels([label("old", "DEFECT")], ["old"], require_classification=True)
    assert validate_labels([label("old", "DEFECT")], ["old"])
    custom = {**label("new", "DEFECT"), "defect_types": ["novel_mechanism"]}
    with pytest.raises(ValueError, match="needs a headline"):
        validate_labels([custom], ["new"], require_classification=True)
    custom["headline"] = "Asks for a 4-point DFT but the sequence has 8 entries."
    assert validate_labels([custom], ["new"], require_classification=True)


def test_a_defects_headline_is_one_plain_sentence_about_the_item():
    """The page prints the headline; the explanation holds the evidence."""
    row = {**label("q", "DEFECT"), "defect_types": ["incorrect_reference_or_test"]}
    for bad, why in (
        ("The key is wrong. The rationale says E.", "2 sentences"),
        ("Independent probes established that the key is wrong.", "says how the audit went"),
        (" ".join(["word"] * 26) + ".", "26 words"),
    ):
        with pytest.raises(ValueError, match=why):
            validate_labels([{**row, "headline": bad}], ["q"], require_classification=True)
    ok = {**row, "headline": "The author's own rationale matches option E, but the key is D."}
    assert validate_labels([ok], ["q"], require_classification=True)[0].headline.endswith("D.")
    # historical labels carry no headline and are still read
    assert validate_labels([label("old", "DEFECT")], ["old"])[0].headline is None


def test_labels_from_two_checks_combine_per_question():
    """Phase 1 labels the item as written, phase 2 its recorded grades; either defect counts."""
    blind = [label(q, "NO_ISSUE_FOUND") for q in ("a", "b", "c", "d")]
    graded = [
        {**label("a", "DEFECT"), "check": "grading-labels", "defect_types": ["false_negative"]},
        {**label("b", "NO_ISSUE_FOUND"), "check": "grading-labels"},
        {**label("c", "UNRESOLVED"), "check": "grading-labels"},
        # d: the grading audit ran on the sample but skipped d
    ]
    result = coverage_summary(["a", "b", "c", "d"], blind + graded)
    assert {r["question_id"]: r["status"] for r in result["questions"]} == {
        "a": "DEFECT",
        "b": "NO_ISSUE_FOUND",
        "c": "UNRESOLVED",
        "d": "NOT_ASSESSED",
    }
    assert result["questions"][0]["by_check"] == {
        "question-labels": "NO_ISSUE_FOUND",
        "grading-labels": "DEFECT",
    }
    assert result["counts_by_check"]["question-labels"]["NO_ISSUE_FOUND"] == 4
    assert result["counts_by_check"]["grading-labels"] == {
        "NO_ISSUE_FOUND": 1,
        "DEFECT": 1,
        "UNRESOLVED": 1,
        "NOT_ASSESSED": 1,
    }
    assert result["defect_counts"] == {"false_negative": 1}
    # untagged labels are phase-1 labels, and one check alone behaves as before
    alone = coverage_summary(["a", "b"], [label("a", "DEFECT"), label("b", "NO_ISSUE_FOUND")])
    assert list(alone["counts_by_check"]) == ["question-labels"]
    assert alone["counts"]["DEFECT"] == 1
    # within one check, two auditors who disagree leave the question unresolved
    split = coverage_summary(
        ["a"],
        [
            {**label("a", "DEFECT"), "check": "grading-labels", "defect_types": ["false_positive"]},
            {**label("a", "NO_ISSUE_FOUND"), "check": "grading-labels"},
        ],
    )
    assert split["questions"][0]["status"] == "UNRESOLVED"


def test_grading_labels_round_trip_into_coverage_tagged(tmp_path):
    """export_coverage reads the grading audit's labels and tags them with its check."""
    import json

    from inspect_ai import Task, eval
    from inspect_ai.dataset import Sample
    from inspect_ai.solver import solver

    from inspect_audit._agent import Evidence, audit_items, item_scorer, record_verdict
    from inspect_audit._coverage import export_coverage

    item = audit_items(["grading-labels"])[0]
    assert item.tools == ["grade"] and "misgraded" in item.details
    record = record_verdict([item])

    @solver
    def review():
        async def solve(state, generate):
            row = {
                **label("1", "DEFECT"),
                "defect_types": ["false_negative"],
                "headline": "The grader rejects the reference answer given with its middle name.",
            }
            await record(
                item="grading-labels",
                grade="DEFECT",
                approaches="reviewed",
                tried="checked",
                remarks="",
                evidence=[Evidence(observed="check", source="log.eval sample=1")],
                details=json.dumps(
                    {"question_assessments": [row], "misgraded": [{"question_id": "1"}]}
                ),
            )
            return state

        return solve

    task = Task(
        dataset=[Sample(id="1", input="review", metadata={"benchmark_metadata": {}})],
        solver=review(),
        scorer=item_scorer(item),
    )
    log = eval(task, model="mockllm/model", log_dir=str(tmp_path / "logs"), display="none")[0]
    assert log.status == "success", log.error
    result = export_coverage([log.location], ["1"], tmp_path / "coverage.json")
    assert result["counts"]["DEFECT"] == 1
    assert result["questions"][0]["assessments"][0]["check"] == "grading-labels"
    assert result["counts_by_check"] == {
        "grading-labels": {"NO_ISSUE_FOUND": 0, "DEFECT": 1, "UNRESOLVED": 0, "NOT_ASSESSED": 0}
    }
