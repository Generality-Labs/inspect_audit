"""The export: the JSON the table site reads, from the reviewed current view."""

from datetime import UTC, datetime

from inspect_audit.findings.models import Outcome, Run, SampleLocation, Source
from inspect_audit.findings.review import IssueEntry, Review, SuppressionRule, apply_review

EVAL = "inspect_evals/stereoset"
T0 = datetime(2026, 10, 5, 11, 24, 42, tzinfo=UTC)
T1 = datetime(2026, 10, 6, 4, 24, 42, tzinfo=UTC)
NOW = datetime(2026, 10, 7, 4, 20, 50, tzinfo=UTC)
AUTHOR = "Matt Fisher <m@x>"


def _dataset_run(run: Run, *, id: str = "dataset-1", timestamp: datetime = T1) -> Run:
    base = run.findings[0]
    findings = [
        base.model_copy(
            update={
                "id": None,
                "run_id": id,
                "producer": "inspect_dataset",
                "rule": "duplicate_questions",
                "dimension": "dataset",
                "fingerprint": f"sha256:dup{i}",
                "locations": [SampleLocation(role="primary", dataset="d", sample_id=str(i))],
                "source": Source(format="x", record={"secret": True}),
            }
        )
        for i in range(2)
    ]
    outcomes = [
        Outcome(rule="duplicate_questions", status="fail", message="2 groups"),
        Outcome(rule="answer_length", status="skip", message="no scorer"),
        Outcome(rule="mojibake", status="pass"),
    ]
    updated = run.model_copy(
        update={
            "id": id,
            "producer": "inspect_dataset",
            "timestamp": timestamp,
            "findings": findings,
            "outcomes": outcomes,
        }
    )
    return Run.model_validate(updated.model_dump())


def _skip_run(run: Run) -> Run:
    return run.model_copy(
        update={
            "id": "header-1",
            "producer": "inspect_audit_header",
            "timestamp": T1,
            "findings": [],
            "outcomes": [
                Outcome(rule="inspect_audit_header", status="skip", message="no logs matched")
            ],
        }
    )


def test_seen_range_spans_the_history_by_fingerprint(run: Run) -> None:
    from inspect_audit.findings.export import seen_range

    older = run.model_copy(update={"id": "lint-0", "timestamp": T0})
    newer = run.model_copy(update={"id": "lint-1", "timestamp": T1})
    seen = seen_range([newer, older])
    assert seen["sha256:0"] == (T0, T1)


def test_index_lists_active_findings_with_seen_dates_issue_links_and_eval_rollups(
    run: Run,
) -> None:
    from inspect_audit.findings.export import index_document, seen_range

    lint = run.model_copy(update={"timestamp": T1})
    dataset = _dataset_run(run)
    review = Review(
        suppressions=[
            SuppressionRule(
                rule="duplicate_questions",
                subject=EVAL,
                author=AUTHOR,
                reason="known",
                since=NOW.date(),
            )
        ],
        issues=[
            IssueEntry(
                id="ISS-0001",
                title="shuffle",
                subject=EVAL,
                findings=["sha256:0"],
                author=AUTHOR,
                opened=NOW.date(),
                github="https://github.com/x/y/issues/1",
            )
        ],
    )
    reviewed = apply_review([lint, dataset, _skip_run(run)], review)
    history = [run.model_copy(update={"id": "lint-0", "timestamp": T0}), *reviewed]
    doc = index_document({EVAL: reviewed}, review, seen_range(history), NOW)

    assert doc["schema"] == 1 and doc["generated_at"] == "2026-10-07T04:20:50Z"
    (entry,) = doc["evals"]
    assert entry["eval"] == EVAL and entry["slug"] == "inspect-evals-stereoset"
    assert entry["revision"] == {
        "commit": "5687c5cdf",
        "package_version": "0.21.1.dev24+g5687c5cdf",
        "dirty": False,
    }
    assert entry["task_version"] == "3-A"
    assert entry["last_run"] == "2026-10-06T04:24:42Z"
    assert entry["producers"]["inspect_audit_header"]["skipped"] == "no logs matched"
    assert entry["producers"]["inspect_evals_lint"]["skipped"] is None
    assert entry["producers"]["inspect_evals_lint"]["run_id"] == "lint-1"
    assert (entry["active"], entry["suppressed"], entry["issues"]) == (1, 2, 1)

    (row,) = doc["findings"]  # suppressed duplicate_questions rows are not here
    assert row["id"] == "lint-1/1" and row["fingerprint"] == "sha256:0"
    assert row["rule"] == "IEBP008"
    assert row["location"] == "code:src/inspect_evals/stereoset/stereoset.py:64"
    assert row["issue"] == "ISS-0001" and row["github"] == "https://github.com/x/y/issues/1"
    assert row["first_seen"] == "2026-10-05T11:24:42Z"
    assert row["last_seen"] == "2026-10-06T04:24:42Z"
    assert "source" not in row and "@" not in str(doc)


def test_index_marks_an_eval_whose_only_run_skipped(run: Run) -> None:
    from inspect_audit.findings.export import index_document

    doc = index_document({EVAL: [_skip_run(run)]}, Review(), {}, NOW)
    (entry,) = doc["evals"]
    assert entry["active"] == 0
    assert entry["producers"]["inspect_audit_header"]["skipped"] == "no logs matched"
    assert doc["findings"] == []


def test_index_finding_without_a_linked_issue_has_null_github(run: Run) -> None:
    from inspect_audit.findings.export import index_document, seen_range

    issue = IssueEntry(
        id="ISS-0001",
        title="t",
        subject=EVAL,
        findings=["sha256:0"],
        author=AUTHOR,
        opened=NOW.date(),
    )
    review = Review(issues=[issue])
    reviewed = apply_review([run], review)
    (row,) = index_document({EVAL: reviewed}, review, seen_range(reviewed), NOW)["findings"]
    assert row["issue"] == "ISS-0001" and row["github"] is None
    (unlinked,) = index_document({EVAL: [run]}, Review(), seen_range([run]), NOW)["findings"]
    assert unlinked["issue"] is None and unlinked["github"] is None


def test_findings_absent_from_the_current_view_are_not_exported(run: Run) -> None:
    from inspect_audit.findings.export import index_document, seen_range

    gone = run.findings[0].model_copy(update={"id": None, "fingerprint": "sha256:gone"})
    older = Run.model_validate(
        run.model_copy(update={"id": "lint-0", "timestamp": T0, "findings": [gone]}).model_dump()
    )
    doc = index_document({EVAL: [run]}, Review(), seen_range([older, run]), NOW)
    assert [r["fingerprint"] for r in doc["findings"]] == ["sha256:0"]


def test_eval_document_has_inputs_runs_groups_suppressed_and_issues(run: Run) -> None:
    from inspect_audit.findings.export import eval_document, seen_range

    lint = run.model_copy(
        update={
            "timestamp": T1,
            "inputs": {"comparison": {"commit": "abc", "task_version": "3-A"}},
        }
    )
    dataset = _dataset_run(run).model_copy(
        update={
            "inputs": {
                "dataset": {
                    "path": "McGill-NLP/stereoset",
                    "mode": "task",
                    "scorers": ["inspect_ai/exact"],
                }
            }
        }
    )
    review = Review(
        suppressions=[
            SuppressionRule(
                rule="duplicate_questions",
                subject=EVAL,
                producer="inspect_dataset",
                author=AUTHOR,
                reason="known",
                since=NOW.date(),
            ),
            SuppressionRule(
                rule="nothing_here",
                subject="*",
                author=AUTHOR,
                reason="never matches",
                since=NOW.date(),
            ),
            SuppressionRule(
                rule="IEBP008",
                subject="inspect_evals/other",
                author=AUTHOR,
                reason="elsewhere",
                since=NOW.date(),
            ),
        ],
        issues=[
            IssueEntry(
                id="ISS-0001",
                title="shuffle",
                subject=EVAL,
                findings=["sha256:0"],
                author=AUTHOR,
                opened=NOW.date(),
                reason="checked",
                github="https://github.com/x/y/issues/1",
            )
        ],
    )
    reviewed = apply_review([lint, dataset, _skip_run(run)], review)
    doc = eval_document(EVAL, reviewed, review, seen_range(reviewed), NOW)

    assert doc["schema"] == 1 and doc["eval"] == EVAL
    assert doc["slug"] == "inspect-evals-stereoset"
    assert doc["inputs"]["dataset"]["scorers"] == ["inspect_ai/exact"]
    assert doc["inputs"]["comparison"] == {"commit": "abc", "task_version": "3-A"}
    by_producer = {r["producer"]: r for r in doc["runs"]}
    assert by_producer["inspect_audit_header"]["skipped"] == "no logs matched"
    assert by_producer["inspect_dataset"]["passing"] == 1
    assert [o["rule"] for o in by_producer["inspect_dataset"]["outcomes"]] == [
        "duplicate_questions",
        "answer_length",
    ]
    (group,) = doc["groups"]  # only the active lint finding; the dataset rows are suppressed
    assert (group["producer"], group["rule"], group["count"]) == (
        "inspect_evals_lint",
        "IEBP008",
        1,
    )
    (finding,) = group["findings"]
    assert finding["locations"] == [
        {
            "kind": "code",
            "role": "primary",
            "quote": None,
            "file": "src/inspect_evals/stereoset/stereoset.py",
            "line": 64,
            "end_line": None,
            "column": 15,
        }
    ]
    assert finding["issue"] == "ISS-0001" and "source" not in finding
    assert doc["suppressed"] == [
        {
            "producer": "inspect_dataset",
            "rule": "duplicate_questions",
            "count": 2,
            "kind": "false_positive",
            "author": "Matt Fisher",
            "reason": "known",
            "since": "2026-10-07",
        }
    ]
    assert doc["issues"] == [
        {
            "id": "ISS-0001",
            "title": "shuffle",
            "author": "Matt Fisher",
            "opened": "2026-10-07",
            "reason": "checked",
            "github": "https://github.com/x/y/issues/1",
            "current": 1,
        }
    ]
    assert "@" not in str(doc)


def test_eval_document_empty_but_checked(run: Run) -> None:
    from inspect_audit.findings.export import eval_document

    clean = run.model_copy(
        update={"findings": [], "outcomes": [Outcome(rule="IEBP008", status="pass")]}
    )
    doc = eval_document(EVAL, [clean], Review(), {}, NOW)
    assert doc["groups"] == [] and doc["suppressed"] == [] and doc["issues"] == []
    assert doc["runs"][0]["passing"] == 1 and doc["runs"][0]["outcomes"] == []
