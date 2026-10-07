"""Deterministic markdown from runs; no model, byte-stable."""

from datetime import UTC, date, datetime

from inspect_audit.findings.models import (
    Outcome,
    Provenance,
    Run,
    SampleLocation,
    Source,
    Suppression,
)
from inspect_audit.findings.render import (
    NOISE_THRESHOLD,
    render_eval_summary,
    render_sweep_summary,
)
from inspect_audit.findings.review import IssueEntry


def _noisy(run: Run, count: int) -> Run:
    base = run.findings[0]
    noisy = [
        base.model_copy(
            update={
                "producer": "inspect_dataset",
                "rule": "answer_length",
                "severity": "none",
                "locations": [SampleLocation(role="primary", dataset="d", sample_id=str(i))],
                "fingerprint": f"sha256:{i}",
                "source": Source(format="inspect_dataset.Finding@0.4.0", record={"i": i}),
            }
        )
        for i in range(count)
    ]
    return run.model_copy(
        update={"id": "dataset-1", "producer": "inspect_dataset", "findings": noisy}
    )


def test_eval_summary_has_subject_outcomes_and_findings(run: Run) -> None:
    run = run.model_copy(
        update={"outcomes": [Outcome(rule="IEBP008", status="fail", message="dup filter")]}
    )
    text = render_eval_summary([run])
    assert text.startswith("# inspect_evals/stereoset\n")
    assert "| Revision | 5687c5cdf" in text
    assert "| Task version | 3-A" in text
    assert "| inspect_evals_lint | IEBP008 | fail | dup filter |" in text
    assert "| inspect_evals_lint | 1 | 0 |" in text
    assert "| dataset | minor | 1 |" in text
    assert (
        "- minor · dataset · IEBP008 · `code:src/inspect_evals/stereoset/stereoset.py:64` · filter_duplicate_ids() without max_duplicates= or reason="
        in text
    )


def test_eval_summary_flags_noisy_rules(run: Run) -> None:
    text = render_eval_summary([run, _noisy(run, NOISE_THRESHOLD + 1)])
    assert f"answer_length produced {NOISE_THRESHOLD + 1} findings" in text
    assert text.count("- none · dataset · answer_length") == 1  # grouped, not one line per row
    assert f"answer_length · {NOISE_THRESHOLD + 1} observations" in text


def test_eval_summary_marks_a_skipped_producer(run: Run) -> None:
    skipped = run.model_copy(
        update={
            "id": "dataset-1",
            "producer": "inspect_dataset",
            "findings": [],
            "outcomes": [
                Outcome(rule="inspect_dataset", status="skip", message="no huggingface asset")
            ],
        }
    )
    text = render_eval_summary([run, skipped])
    assert "| inspect_dataset | inspect_dataset | skip | no huggingface asset |" in text
    assert "inspect_dataset: skipped" in text


def test_sweep_summary_one_row_per_eval(run: Run) -> None:
    other = run.model_copy(
        update={
            "id": "lint-2",
            "subject": run.subject.model_copy(update={"eval": "inspect_evals/hle"}),
            "findings": [],
        }
    )
    text = render_sweep_summary({"inspect_evals/hle": [other], "inspect_evals/stereoset": [run]})
    lines = [line for line in text.splitlines() if line.startswith("| inspect_evals/")]
    assert lines[0].startswith("| inspect_evals/hle |")
    assert lines[1].startswith("| inspect_evals/stereoset |")
    assert "| inspect_evals/stereoset | inspect_evals_lint: 1 finding" in text


def test_rendering_is_deterministic(run: Run) -> None:
    assert render_eval_summary([run]) == render_eval_summary([run])


def _header_run(run: Run) -> Run:
    return run.model_copy(
        update={
            "id": "header-1",
            "producer": "inspect_audit_header",
            "findings": [],
            "outcomes": [Outcome(rule="header.dataset_samples", status="pass")],
            "inputs": {
                "logs": {
                    "used": ["/logs/a.eval", "/logs/b.eval"],
                    "excluded": [{"path": "/logs/mock.eval", "reason": "mock model mockllm/model"}],
                    "count_excluded": [
                        {
                            "path": "/logs/b.eval",
                            "reason": "task args {'subset': 'small'} differ from the default configuration",
                        }
                    ],
                },
                "comparison": {
                    "commit": "5687c5cdf",
                    "package_version": "0.21.1",
                    "task_version": "3-A",
                },
            },
        }
    )


def _dataset_run(run: Run) -> Run:
    return run.model_copy(
        update={
            "id": "dataset-1",
            "producer": "inspect_dataset",
            "findings": [],
            "inputs": {
                "dataset": {
                    "path": "McGill-NLP/stereoset",
                    "config": "intersentence",
                    "split": "validation",
                    "revision": None,
                    "fields": {"question": "context"},
                    "declared": True,
                }
            },
        }
    )


def test_eval_summary_has_an_inputs_section(run: Run) -> None:
    text = render_eval_summary([run, _header_run(run), _dataset_run(run)])
    inputs = text.split("## Inputs", 1)[1].split("## Outcomes", 1)[0]
    assert "McGill-NLP/stereoset" in inputs and "intersentence" in inputs and "declared" in inputs
    assert "2 log(s) used" in inputs
    assert "1 excluded" in inputs and "mock model mockllm/model" in inputs
    assert "1 not compared for sample count" in inputs and "subset" in inputs
    assert "compared against 5687c5cdf" in inputs and "3-A" in inputs


def test_eval_summary_inputs_section_names_a_task_scan(run: Run) -> None:
    task_run = _dataset_run(run).model_copy(
        update={
            "inputs": {
                "dataset": {
                    "path": "inspect_evals/stereoset",
                    "config": None,
                    "split": None,
                    "revision": None,
                    "fields": {},
                    "declared": False,
                    "mode": "task",
                    "task": "stereoset",
                }
            }
        }
    )
    text = render_eval_summary([run, task_run])
    inputs = text.split("## Inputs", 1)[1].split("## Outcomes", 1)[0]
    assert (
        "- Dataset scanned: `inspect_evals/stereoset` (through the task's own loader), "
        "inferred from eval.yaml." in inputs
    )


def test_eval_summary_inputs_section_says_when_nothing_was_declared(run: Run) -> None:
    text = render_eval_summary([run])
    inputs = text.split("## Inputs", 1)[1].split("## Outcomes", 1)[0]
    assert "no dataset scan" in inputs and "no logs" in inputs


def test_sweep_summary_shows_log_counts(run: Run) -> None:
    text = render_sweep_summary({"inspect_evals/stereoset": [run, _header_run(run)]})
    assert "inspect_audit_header: 0 findings (2 logs, 1 excluded)" in text


def test_inputs_section_lists_excluded_logs_when_none_were_used(run: Run) -> None:
    skipped = run.model_copy(
        update={
            "id": "header-skip",
            "producer": "inspect_audit_header",
            "findings": [],
            "outcomes": [
                Outcome(
                    rule="inspect_audit_header",
                    status="skip",
                    message="1 matching log(s), all excluded: mock model mockllm/model",
                )
            ],
            "inputs": {
                "logs": {
                    "used": [],
                    "excluded": [{"path": "/logs/mock.eval", "reason": "mock model mockllm/model"}],
                    "count_excluded": [],
                }
            },
        }
    )
    text = render_eval_summary([skipped])
    inputs = text.split("## Inputs", 1)[1].split("## Outcomes", 1)[0]
    assert "no logs examined" not in inputs
    assert "0 log(s) used, 1 excluded" in inputs and "/logs/mock.eval" in inputs


def _suppressed(run: Run) -> Run:
    finding = run.findings[0].model_copy(
        update={
            "suppressions": [
                Suppression(
                    kind="false_positive",
                    provenance=Provenance(
                        timestamp=datetime(2026, 9, 30, tzinfo=UTC),
                        author="matt",
                        reason="struct answers",
                    ),
                )
            ]
        }
    )
    return run.model_copy(update={"findings": [finding]})


def test_grouped_findings_show_count_and_examples(run: Run) -> None:
    text = render_eval_summary([_noisy(run, 3)])
    assert "- none · dataset · answer_length · 3 observations" in text
    assert text.count("  - `sample:d:") == 2  # EXAMPLES


def test_suppressed_findings_are_counted_and_listed_separately(run: Run) -> None:
    text = render_eval_summary([_suppressed(run)])
    assert "| inspect_evals_lint | 0 | 1 |" in text
    assert "## Suppressed" in text
    assert (
        "- inspect_evals_lint · IEBP008 · 1 observation · false_positive · matt: struct answers"
        in text
    )
    assert "- minor · dataset · IEBP008" not in text.split("## Findings", 1)[1]


def test_issues_section_lists_current_and_missing_observations(run: Run) -> None:
    linked = run.model_copy(
        update={"findings": [run.findings[0].model_copy(update={"issue": "ISS-0007"})]}
    )
    opened = date(2026, 9, 30)
    issues = [
        IssueEntry(
            id="ISS-0007",
            title="dup filter",
            subject="inspect_evals/stereoset",
            findings=["sha256:0"],
            author="matt",
            opened=opened,
            github="https://github.com/x/y/issues/1",
        ),
        IssueEntry(
            id="ISS-0008",
            title="gone",
            subject="inspect_evals/stereoset",
            findings=["sha256:gone"],
            author="matt",
            opened=opened,
        ),
        IssueEntry(
            id="ISS-0009",
            title="other eval",
            subject="inspect_evals/hle",
            findings=["sha256:z"],
            author="matt",
            opened=opened,
        ),
    ]
    text = render_eval_summary([linked], issues=issues)
    section = text.split("## Issues", 1)[1]
    assert (
        "- ISS-0007 · dup filter · 1 current observation · https://github.com/x/y/issues/1"
        in section
    )
    assert "- ISS-0008 · gone · 0 current observations · no current observation" in section
    assert "ISS-0009" not in section


def test_sweep_summary_counts_active_and_suppressed(run: Run) -> None:
    text = render_sweep_summary({"inspect_evals/stereoset": [_suppressed(run)]})
    assert "inspect_evals_lint: 0 findings, 1 suppressed" in text


def test_inputs_data_returns_what_the_lines_are_rendered_from(run: Run) -> None:
    from inspect_audit.findings.render import inputs_data

    dataset_run = run.model_copy(
        update={
            "inputs": {
                "dataset": {
                    "path": "McGill-NLP/stereoset",
                    "mode": "task",
                    "declared": True,
                    "samples": 2123,
                    "scorers": ["inspect_ai/exact"],
                },
                "logs": {
                    "used": [{"path": "a.eval"}],
                    "excluded": [{"path": "b.eval", "reason": "mock model"}],
                    "count_excluded": [],
                },
                "comparison": {"commit": "abc", "task_version": "3-A"},
            }
        }
    )
    data = inputs_data([dataset_run])
    assert data["dataset"]["scorers"] == ["inspect_ai/exact"]
    assert data["logs"]["excluded"] == [{"path": "b.eval", "reason": "mock model"}]
    assert data["comparison"]["commit"] == "abc"
    assert inputs_data([run]) == {
        "dataset": {},
        "logs": {"used": [], "excluded": [], "count_excluded": []},
        "comparison": {},
    }
