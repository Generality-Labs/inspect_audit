"""One eval's reviewed findings as hypotheses for an agent, with record ids to cite."""

from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from inspect_audit.findings.cli import write_outputs
from inspect_audit.findings.leads import (
    EXAMPLES,
    NoRunsError,
    leads_markdown,
    render_leads,
    select_leads,
)
from inspect_audit.findings.models import (
    Outcome,
    Provenance,
    Run,
    SampleLocation,
    Source,
    Suppression,
    TranscriptLocation,
)
from inspect_audit.findings.review import IssueEntry, Review, SuppressionRule


def _dataset_run(run: Run, count: int) -> Run:
    base = run.findings[0]
    findings = [
        base.model_copy(
            update={
                "id": None,
                "run_id": "dataset-1",
                "producer": "inspect_dataset",
                "rule": "duplicate_questions",
                "severity": "none",
                "summary": f"Question {i} appears twice",
                "locations": [SampleLocation(role="primary", dataset="d", sample_id=str(i))],
                "fingerprint": f"sha256:dup{i}",
                "source": Source(format="inspect_dataset.Finding@0.4.0", record={"i": i}),
            }
        )
        for i in range(count)
    ]
    return Run.model_validate(
        run.model_copy(
            update={
                "id": "dataset-1",
                "producer": "inspect_dataset",
                "findings": findings,
                "outcomes": [
                    Outcome(
                        rule="duplicate_questions", status="fail", message=f"{count} finding(s)"
                    ),
                    Outcome(
                        rule="answer_length",
                        status="skip",
                        message="not applicable: struct answers",
                    ),
                ],
            }
        ).model_dump()
    )


def _transcript_run(run: Run) -> Run:
    finding = run.findings[0].model_copy(
        update={
            "id": None,
            "run_id": "scout-1",
            "producer": "inspect_scout",
            "rule": "harness_error",
            "severity": "major",
            "status": "hypothesis",
            "summary": "ImportError in the test harness",
            "locations": [
                TranscriptLocation(role="primary", eval_id="ev1", sample_uuid="u7", sample_id="7")
            ],
            "fingerprint": "sha256:scout7",
        }
    )
    return Run.model_validate(
        run.model_copy(
            update={"id": "scout-1", "producer": "inspect_scout", "findings": [finding]}
        ).model_dump()
    )


def test_select_leads_drops_suppressed_and_counts_them(run: Run) -> None:
    suppressed = run.findings[0].model_copy(
        update={
            "suppressions": [
                Suppression(
                    kind="false_positive",
                    provenance=Provenance(
                        timestamp=datetime(2026, 9, 30, tzinfo=UTC), author="matt", reason="r"
                    ),
                )
            ]
        }
    )
    runs = [run.model_copy(update={"findings": [suppressed]}), _dataset_run(run, 2)]
    selected_runs, findings, dropped = select_leads(runs, "inspect_evals/stereoset")
    assert len(selected_runs) == 2
    assert {f.rule for f in findings} == {"duplicate_questions"} and dropped == 1


def test_select_leads_by_sample_matches_sample_and_transcript_locations(run: Run) -> None:
    runs = [_dataset_run(run, 10), _transcript_run(run), run]
    _, findings, _ = select_leads(runs, "inspect_evals/stereoset", sample_id="7")
    assert sorted(f.rule for f in findings) == ["duplicate_questions", "harness_error"]
    assert all(f.id for f in findings)  # record ids are present to cite


def test_select_leads_with_no_runs_for_the_eval_raises(run: Run) -> None:
    with pytest.raises(NoRunsError, match="inspect_evals/hle"):
        select_leads([run], "inspect_evals/hle")


def test_render_leads_groups_caps_and_lists_skips(run: Run) -> None:
    runs = [_dataset_run(run, EXAMPLES + 5), run]
    selected_runs, findings, dropped = select_leads(runs, "inspect_evals/stereoset")
    text = render_leads("inspect_evals/stereoset", selected_runs, findings, dropped, [])
    assert text.startswith("# Leads for inspect_evals/stereoset\n")
    assert "hypotheses" in text and "record id" in text  # the preamble says what a lead is
    assert (
        f"inspect_dataset · duplicate_questions · dataset · none · {EXAMPLES + 5} observations"
        in text
    )
    assert text.count("`dataset-1/") == EXAMPLES  # capped examples, each with its record id
    assert "and 5 more in `runs/dataset-1.run.json`" in text
    assert "`lint-1/1` · `code:src/inspect_evals/stereoset/stereoset.py:64`" in text
    assert "## Not examined" in text and "answer_length" in text and "struct answers" in text
    assert "0 suppressed" in text


def test_render_leads_shows_accepted_issues_and_suppressed_count(run: Run) -> None:
    issue = IssueEntry(
        id="ISS-0007",
        title="dup filter",
        subject="inspect_evals/stereoset",
        findings=["sha256:0"],
        author="matt",
        opened=date(2026, 9, 30),
    )
    linked = run.model_copy(
        update={"findings": [run.findings[0].model_copy(update={"issue": "ISS-0007"})]}
    )
    text = render_leads("inspect_evals/stereoset", [linked], linked.findings, 3, [issue])
    assert "## Already accepted as issues" in text
    assert "- ISS-0007 · dup filter · 1 current observation" in text
    assert "3 suppressed" in text
    # an accepted observation is still listed as a lead, marked with its issue
    assert "(issue ISS-0007)" in text


def test_leads_markdown_reads_the_current_view_and_applies_review(tmp_path: Path, run: Run) -> None:
    out = tmp_path / "out"
    write_outputs(out, {"inspect_evals/stereoset": [run, _dataset_run(run, 2)]})
    review = Review(
        suppressions=[
            SuppressionRule(rule="IEBP008", author="matt", reason="r", since=date(2026, 9, 30))
        ]
    )
    text = leads_markdown(out, "inspect_evals/stereoset", review)
    leads_section = text.split("## Leads", 1)[1].split("## Not examined", 1)[0]
    assert "IEBP008" not in leads_section
    assert "1 suppressed" in text
    assert "duplicate_questions" in text
    with pytest.raises(NoRunsError):
        leads_markdown(out, "inspect_evals/hle", review)


def test_sample_scope_counts_only_that_samples_suppressions_and_names_the_rest(run: Run) -> None:
    dataset = _dataset_run(run, 6)
    suppression = Suppression(
        kind="false_positive",
        provenance=Provenance(timestamp=datetime(2026, 9, 30, tzinfo=UTC), author="m", reason="r"),
    )
    marked = [
        finding.model_copy(update={"suppressions": [suppression]}) if i in (3, 4) else finding
        for i, finding in enumerate(dataset.findings)
    ]
    dataset = dataset.model_copy(update={"findings": marked})
    runs = [dataset, run]  # `run` holds one eval-wide code finding
    selected_runs, findings, dropped = select_leads(runs, "inspect_evals/stereoset", sample_id="1")
    assert [f.rule for f in findings] == ["duplicate_questions"] and dropped == 0
    text = render_leads(
        "inspect_evals/stereoset", selected_runs, findings, dropped, [], sample_id="1"
    )
    assert "0 suppressed" in text
    assert "eval-wide observation" in text and "not shown" in text  # the code finding was left out


def test_group_count_matches_the_headings(run: Run) -> None:
    dataset = _dataset_run(run, 4)
    mixed = [
        f.model_copy(update={"severity": "major" if i % 2 else "none"})
        for i, f in enumerate(dataset.findings)
    ]
    dataset = dataset.model_copy(update={"findings": mixed})
    _, findings, dropped = select_leads([dataset], "inspect_evals/stereoset")
    text = render_leads("inspect_evals/stereoset", [dataset], findings, dropped, [])
    headings = text.count("\n### ")
    assert headings == 2 and f"in {headings} group(s)" in text


def test_skip_messages_are_one_line_and_bounded(run: Run) -> None:
    long_reason = (
        "inspect-dataset exit 1: " + "x" * 400 + "\nTraceback (most recent call last):\n  File ..."
    )
    skipped = run.model_copy(
        update={
            "id": "dataset-1",
            "producer": "inspect_dataset",
            "findings": [],
            "outcomes": [
                Outcome(rule="inspect_dataset", status="skip", message=long_reason),
                Outcome(rule="answer_length", status="skip", message=None),
            ],
        }
    )
    text = render_leads("inspect_evals/stereoset", [skipped], [], 0, [])
    section = text.split("## Not examined", 1)[1]
    assert "Traceback" not in section and "\n  File" not in section
    assert "whole producer did not run" in section
    assert "(no reason recorded)" in section
    assert all(len(line) <= 260 for line in section.splitlines())


def test_all_runs_skipped_says_so_on_the_summary_line(run: Run) -> None:
    skipped = run.model_copy(
        update={
            "findings": [],
            "outcomes": [Outcome(rule="inspect_evals_lint", status="skip", message="lint exit 2")],
        }
    )
    text = render_leads("inspect_evals/stereoset", [skipped], [], 0, [])
    assert "No producer ran for this eval" in text.split("## Leads", 1)[1].split("###", 1)[0]


def test_preamble_speaks_to_both_agents(run: Run) -> None:
    text = render_leads("inspect_evals/stereoset", [run], run.findings, 0, [])
    preamble = text.split("## Inputs", 1)[0]
    assert "automated producer" in preamble and "worker verdict" not in preamble
    assert "suspected mechanism" in preamble and "record id" in preamble
