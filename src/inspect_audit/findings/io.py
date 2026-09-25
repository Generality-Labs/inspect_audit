"""Runs as JSON files, findings as dataframes and parquet."""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pandas as pd

from .models import Run

FINDING_COLUMNS: tuple[str, ...] = (
    "fingerprint",
    "fingerprint_version",
    "schema_version",
    "subject_eval",
    "subject_revision_commit",
    "subject_revision_package_version",
    "subject_task_version_full",
    "subject_dataset_path",
    "dimension",
    "severity",
    "status",
    "summary",
    "primary_kind",
    "primary_key",
    "run_id",
    "producer",
    "rule",
    "source_format",
    "source",
    "locations",
)

RUN_COLUMNS: tuple[str, ...] = (
    "run_id",
    "producer",
    "producer_version",
    "subject_eval",
    "timestamp",
    "duration_s",
    "outcomes_pass",
    "outcomes_fail",
    "outcomes_skip",
    "findings",
    "skipped",
)


def write_run(run: Run, path: Path) -> Path:
    """Write one run as indented JSON, creating parent directories."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(run.model_dump_json(indent=1) + "\n")
    return path


def read_run(path: Path) -> Run:
    return Run.model_validate_json(path.read_text())


def read_runs(root: Path) -> list[Run]:
    """Every `*.run.json` under `root`, sorted by path."""
    return [read_run(path) for path in sorted(root.rglob("*.run.json"))]


def _finding_records(runs: Sequence[Run]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for run in runs:
        for finding in run.findings:
            primary = finding.primary_location
            records.append(
                {
                    "fingerprint": finding.fingerprint,
                    "fingerprint_version": finding.fingerprint_version,
                    "schema_version": finding.schema_version,
                    "subject_eval": finding.subject.eval,
                    "subject_revision_commit": finding.subject.revision.commit,
                    "subject_revision_package_version": finding.subject.revision.package_version,
                    "subject_task_version_full": finding.subject.task_version.full if finding.subject.task_version else None,
                    "subject_dataset_path": finding.subject.dataset.path if finding.subject.dataset else None,
                    "dimension": finding.dimension,
                    "severity": finding.severity,
                    "status": finding.status,
                    "summary": finding.summary,
                    "primary_kind": primary.kind,
                    "primary_key": primary.key(),
                    "run_id": finding.run_id,
                    "producer": finding.producer,
                    "rule": finding.rule,
                    "source_format": finding.source.format,
                    "source": finding.source.model_dump_json(),
                    "locations": json.dumps([location.model_dump(mode="json") for location in finding.locations]),
                }
            )
    return records


def findings_df(runs: Sequence[Run]) -> pd.DataFrame:
    """One row per finding, envelope flattened, source and locations as JSON strings."""
    return pd.DataFrame.from_records(_finding_records(runs), columns=list(FINDING_COLUMNS))


def runs_df(runs: Sequence[Run]) -> pd.DataFrame:
    """One row per run with outcome counts."""
    records: list[dict[str, Any]] = []
    for run in runs:
        statuses = [outcome.status for outcome in run.outcomes]
        records.append(
            {
                "run_id": run.id,
                "producer": run.producer,
                "producer_version": run.producer_version,
                "subject_eval": run.subject.eval,
                "timestamp": run.timestamp.isoformat(),
                "duration_s": run.duration_s,
                "outcomes_pass": statuses.count("pass"),
                "outcomes_fail": statuses.count("fail"),
                "outcomes_skip": statuses.count("skip"),
                "findings": len(run.findings),
                "skipped": bool(statuses) and all(status == "skip" for status in statuses),
            }
        )
    return pd.DataFrame.from_records(records, columns=list(RUN_COLUMNS))


def write_parquet(frame: pd.DataFrame, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_parquet(path, index=False)
    return path
