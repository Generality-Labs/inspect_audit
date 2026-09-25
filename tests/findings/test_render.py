"""Deterministic markdown from runs; no model, byte-stable."""

from inspect_audit.findings.models import Outcome, Run, SampleLocation, Source
from inspect_audit.findings.render import (
    NOISE_THRESHOLD,
    render_eval_summary,
    render_sweep_summary,
)


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
    return run.model_copy(update={"id": "dataset-1", "producer": "inspect_dataset", "findings": noisy})


def test_eval_summary_has_subject_outcomes_and_findings(run: Run) -> None:
    run = run.model_copy(update={"outcomes": [Outcome(rule="IEBP008", status="fail", message="dup filter")]})
    text = render_eval_summary([run])
    assert text.startswith("# inspect_evals/stereoset\n")
    assert "| Revision | 5687c5cdf" in text
    assert "| Task version | 3-A" in text
    assert "| inspect_evals_lint | IEBP008 | fail | dup filter |" in text
    assert "| inspect_evals_lint | 1 |" in text
    assert "| dataset | minor | 1 |" in text
    assert "- minor · dataset · IEBP008 · `code:src/inspect_evals/stereoset/stereoset.py:64` · filter_duplicate_ids() without max_duplicates= or reason=" in text


def test_eval_summary_flags_noisy_rules(run: Run) -> None:
    text = render_eval_summary([run, _noisy(run, NOISE_THRESHOLD + 1)])
    assert f"answer_length produced {NOISE_THRESHOLD + 1} findings" in text
    assert text.count("- none · dataset · answer_length") == NOISE_THRESHOLD + 1


def test_eval_summary_marks_a_skipped_producer(run: Run) -> None:
    skipped = run.model_copy(
        update={"id": "dataset-1", "producer": "inspect_dataset", "findings": [],
                "outcomes": [Outcome(rule="inspect_dataset", status="skip", message="no huggingface asset")]}
    )
    text = render_eval_summary([run, skipped])
    assert "| inspect_dataset | inspect_dataset | skip | no huggingface asset |" in text
    assert "inspect_dataset: skipped" in text


def test_sweep_summary_one_row_per_eval(run: Run) -> None:
    other = run.model_copy(update={"id": "lint-2", "subject": run.subject.model_copy(update={"eval": "inspect_evals/hle"}), "findings": []})
    text = render_sweep_summary({"inspect_evals/hle": [other], "inspect_evals/stereoset": [run]})
    lines = [line for line in text.splitlines() if line.startswith("| inspect_evals/")]
    assert lines[0].startswith("| inspect_evals/hle |")
    assert lines[1].startswith("| inspect_evals/stereoset |")
    assert "| inspect_evals/stereoset | inspect_evals_lint: 1 finding" in text


def test_rendering_is_deterministic(run: Run) -> None:
    assert render_eval_summary([run]) == render_eval_summary([run])
