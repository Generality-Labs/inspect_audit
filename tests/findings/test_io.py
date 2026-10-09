"""Runs on disk and as dataframes."""

from datetime import UTC, datetime
from pathlib import Path

import pandas as pd

from inspect_audit.findings.fs import StoreFS
from inspect_audit.findings.io import (
    FINDING_COLUMNS,
    findings_df,
    read_current,
    read_run,
    read_runs,
    runs_df,
    write_parquet,
    write_run,
)
from inspect_audit.findings.models import (
    Effect,
    Outcome,
    Provenance,
    Run,
    Suppression,
    VersionRef,
)


def test_write_and_read_a_run(tmp_path: Path, run: Run) -> None:
    fs = StoreFS.from_locator(tmp_path)
    key = write_run(run, fs, "stereoset/runs/lint.run.json")
    assert key == "stereoset/runs/lint.run.json"
    assert read_run(fs, key) == run


def test_read_runs_reads_every_eval_runs_dir(tmp_path: Path, run: Run) -> None:
    fs = StoreFS.from_locator(tmp_path)
    write_run(run, fs, "a/runs/lint.run.json")
    write_run(run.model_copy(update={"id": "lint-2"}), fs, "b/runs/lint.run.json")
    fs.write_text("b/runs/notes.json", "{}")
    fs.write_text("stray.run.json", "{}")  # not under <slug>/runs: not a run of the store
    assert sorted(r.id for r in read_runs(fs)) == ["lint-1", "lint-2"]


def test_runs_read_identically_from_a_directory_and_from_memory(tmp_path: Path, run: Run) -> None:
    stores = [StoreFS.from_locator(tmp_path), StoreFS.from_locator("memory://io-parity")]
    for fs in stores:
        write_run(run, fs, "inspect-evals-stereoset/runs/lint-1.run.json")
    ids = [[r.id for r in read_current(fs)] for fs in stores]
    assert ids == [["lint-1"], ["lint-1"]]
    assert read_runs(stores[0]) == read_runs(stores[1])


def test_findings_df_columns_and_values(run: Run) -> None:
    frame = findings_df([run])
    assert list(frame.columns) == list(FINDING_COLUMNS)
    row = frame.iloc[0]
    assert row["fingerprint"] == "sha256:0"
    assert row["subject_eval"] == "inspect_evals/stereoset"
    assert row["subject_revision_commit"] == "5687c5cdf"
    assert row["subject_task_version_full"] == "3-A"
    assert row["primary_kind"] == "code"
    assert row["primary_key"] == "code:src/inspect_evals/stereoset/stereoset.py:64"
    assert row["producer"] == "inspect_evals_lint"
    assert row["rule"] == "IEBP008"
    assert '"code":"IEBP008"' in row["source"] or '"code": "IEBP008"' in row["source"]
    assert row["locations"].startswith("[")


def test_findings_df_is_empty_but_typed_without_findings(run: Run) -> None:
    frame = findings_df([run.model_copy(update={"findings": []})])
    assert list(frame.columns) == list(FINDING_COLUMNS)
    assert len(frame) == 0


def test_runs_df_counts_outcomes(run: Run) -> None:
    run = run.model_copy(
        update={
            "outcomes": [
                Outcome(rule="a", status="pass"),
                Outcome(rule="b", status="fail"),
                Outcome(rule="c", status="skip"),
            ],
            "duration_s": 1.5,
        }
    )
    frame = runs_df([run])
    row = frame.iloc[0]
    assert (row["outcomes_pass"], row["outcomes_fail"], row["outcomes_skip"]) == (1, 1, 1)
    assert row["findings"] == 1
    assert not bool(row["skipped"])
    assert row["duration_s"] == 1.5


def test_parquet_round_trip(tmp_path: Path, run: Run) -> None:
    fs = StoreFS.from_locator(tmp_path)
    key = write_parquet(findings_df([run]), fs, "findings.parquet")
    again = pd.read_parquet(tmp_path / key)
    assert list(again.columns) == list(FINDING_COLUMNS)
    assert again.iloc[0]["fingerprint"] == "sha256:0"


def test_findings_df_carries_review_and_effect_columns(run: Run) -> None:
    finding = run.findings[0].model_copy(
        update={
            "aliases": ["IEBP-old"],
            "suppressions": [
                Suppression(
                    kind="false_positive",
                    provenance=Provenance(
                        timestamp=datetime(2026, 9, 29, tzinfo=UTC),
                        author="matt",
                        reason="struct answers",
                    ),
                )
            ],
            "effect": Effect(affected=18, denominator=2123, score="hypothesised"),
            "introduced": VersionRef(commit="abc"),
        }
    )
    frame = findings_df([run.model_copy(update={"findings": [finding]})])
    row = frame.iloc[0]
    for column in (
        "subject_revision_dirty",
        "subject_task_version_comparability",
        "subject_task_version_interface",
        "subject_dataset_config",
        "subject_dataset_split",
        "subject_dataset_revision",
        "subject_task_args",
        "aliases",
        "suppressions",
        "suppressed",
        "history",
        "introduced",
        "fixed",
        "effect",
    ):
        assert column in frame.columns, column
    assert row["subject_revision_dirty"] is False or row["subject_revision_dirty"] == False  # noqa: E712
    assert row["subject_task_version_comparability"] == 3
    assert row["aliases"] == '["IEBP-old"]'
    assert "false_positive" in row["suppressions"] and "matt" in row["suppressions"]
    assert bool(row["suppressed"]) is True
    assert '"affected": 18' in row["effect"] or '"affected":18' in row["effect"]
    assert "abc" in row["introduced"] and row["fixed"] is None
    plain = findings_df([run]).iloc[0]
    assert bool(plain["suppressed"]) is False and plain["effect"] is None


def test_current_view_is_the_newest_run_per_producer(tmp_path: Path, run: Run) -> None:
    fs = StoreFS.from_locator(tmp_path)
    older = run.model_copy(update={"id": "lint-1"})
    newer = run.model_copy(update={"id": "lint-2", "timestamp": datetime(2026, 9, 26, tzinfo=UTC)})
    other = run.model_copy(update={"id": "dataset-1", "producer": "inspect_dataset"})
    for r in (newer, older, other):
        write_run(r, fs, f"inspect-evals-stereoset/runs/{r.id}.run.json")
    assert sorted(r.id for r in read_current(fs)) == ["dataset-1", "lint-2"]
    assert sorted(r.id for r in read_runs(fs)) == ["dataset-1", "lint-1", "lint-2"]


def test_current_view_breaks_timestamp_ties_by_file_name(tmp_path: Path, run: Run) -> None:
    fs = StoreFS.from_locator(tmp_path)
    write_run(run, fs, "inspect-evals-stereoset/runs/lint-1.run.json")
    write_run(
        run.model_copy(update={"id": "lint-1-2"}),
        fs,
        "inspect-evals-stereoset/runs/lint-1-2.run.json",
    )
    assert [r.id for r in read_current(fs)] == ["lint-1-2"]


def test_findings_df_has_record_id_and_issue_columns(run: Run) -> None:
    frame = findings_df([run])
    assert frame.columns[0] == "id"
    assert frame.iloc[0]["id"] == "lint-1/1"
    assert "issue" in frame.columns and frame.iloc[0]["issue"] is None
    linked = run.model_copy(
        update={"findings": [run.findings[0].model_copy(update={"issue": "ISS-0001"})]}
    )
    assert findings_df([linked]).iloc[0]["issue"] == "ISS-0001"
