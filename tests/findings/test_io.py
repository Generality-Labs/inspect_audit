"""Runs on disk and as dataframes."""

from pathlib import Path

import pandas as pd

from inspect_audit.findings.io import (
    FINDING_COLUMNS,
    findings_df,
    read_run,
    read_runs,
    runs_df,
    write_parquet,
    write_run,
)
from inspect_audit.findings.models import Outcome, Run


def test_write_and_read_a_run(tmp_path: Path, run: Run) -> None:
    path = write_run(run, tmp_path / "stereoset" / "lint.run.json")
    assert path.name == "lint.run.json"
    assert read_run(path) == run


def test_read_runs_walks_the_tree(tmp_path: Path, run: Run) -> None:
    write_run(run, tmp_path / "a" / "lint.run.json")
    write_run(run.model_copy(update={"id": "lint-2"}), tmp_path / "b" / "lint.run.json")
    (tmp_path / "b" / "notes.json").write_text("{}")
    assert sorted(r.id for r in read_runs(tmp_path)) == ["lint-1", "lint-2"]


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
            "outcomes": [Outcome(rule="a", status="pass"), Outcome(rule="b", status="fail"), Outcome(rule="c", status="skip")],
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
    path = write_parquet(findings_df([run]), tmp_path / "findings.parquet")
    again = pd.read_parquet(path)
    assert list(again.columns) == list(FINDING_COLUMNS)
    assert again.iloc[0]["fingerprint"] == "sha256:0"
