"""Runs as JSON files, findings as dataframes and parquet."""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pandas as pd

from .models import Run

FINDING_COLUMNS: tuple[str, ...] = (
    "id",
    "fingerprint",
    "fingerprint_version",
    "schema_version",
    "subject_eval",
    "subject_revision_commit",
    "subject_revision_package_version",
    "subject_revision_dirty",
    "subject_task_version_full",
    "subject_task_version_comparability",
    "subject_task_version_interface",
    "subject_dataset_path",
    "subject_dataset_config",
    "subject_dataset_split",
    "subject_dataset_revision",
    "subject_task_args",
    "taxonomy",
    "dimension",
    "check",
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
    "aliases",
    "suppressions",
    "suppressed",
    "issue",
    "history",
    "introduced",
    "fixed",
    "effect",
)

CURRENT_FILE = "current.json"

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
    """Every `*.run.json` under `root`, sorted by path: the whole history, not just the current view."""
    return [read_run(path) for path in sorted(root.rglob("*.run.json"))]


def update_current(eval_dir: Path, runs: Sequence[Run]) -> dict[str, str]:
    """Point `current.json` at these runs' files, leaving producers that did not run where they were.

    Run files are immutable and named by run id; this manifest is the only thing a sweep rewrites,
    so a partial sweep never silently mixes stale and fresh results and history is never lost.
    """
    manifest_path = eval_dir / CURRENT_FILE
    current: dict[str, str] = (
        json.loads(manifest_path.read_text()) if manifest_path.is_file() else {}
    )
    for run in runs:
        current[run.producer] = f"runs/{run.id}.run.json"
    eval_dir.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(dict(sorted(current.items())), indent=1) + "\n")
    return current


def read_current(root: Path) -> list[Run]:
    """The runs every `current.json` under `root` selects."""
    runs: list[Run] = []
    for manifest_path in sorted(root.rglob(CURRENT_FILE)):
        current: dict[str, str] = json.loads(manifest_path.read_text())
        runs += [read_run(manifest_path.parent / rel) for rel in current.values()]
    return runs


def _json_or_none(value: Any) -> str | None:
    if value is None or value == []:
        return None
    if hasattr(value, "model_dump"):
        return json.dumps(value.model_dump(mode="json"))
    return json.dumps([v.model_dump(mode="json") if hasattr(v, "model_dump") else v for v in value])


def _finding_records(runs: Sequence[Run]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for run in runs:
        for finding in run.findings:
            primary = finding.primary_location
            task_version = finding.subject.task_version
            dataset = finding.subject.dataset
            records.append(
                {
                    "id": finding.id,
                    "fingerprint": finding.fingerprint,
                    "fingerprint_version": finding.fingerprint_version,
                    "schema_version": finding.schema_version,
                    "subject_eval": finding.subject.eval,
                    "subject_revision_commit": finding.subject.revision.commit,
                    "subject_revision_package_version": finding.subject.revision.package_version,
                    "subject_revision_dirty": finding.subject.revision.dirty,
                    "subject_task_version_full": task_version.full if task_version else None,
                    "subject_task_version_comparability": task_version.comparability
                    if task_version
                    else None,
                    "subject_task_version_interface": task_version.interface
                    if task_version
                    else None,
                    "subject_dataset_path": dataset.path if dataset else None,
                    "subject_dataset_config": dataset.config if dataset else None,
                    "subject_dataset_split": dataset.split if dataset else None,
                    "subject_dataset_revision": dataset.revision if dataset else None,
                    "subject_task_args": json.dumps(finding.subject.task_args)
                    if finding.subject.task_args
                    else None,
                    "taxonomy": finding.taxonomy,
                    "dimension": finding.dimension,
                    "check": finding.check,
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
                    "locations": json.dumps(
                        [location.model_dump(mode="json") for location in finding.locations]
                    ),
                    "aliases": json.dumps(finding.aliases) if finding.aliases else None,
                    "suppressions": _json_or_none(finding.suppressions),
                    "suppressed": bool(finding.suppressions),
                    "issue": finding.issue,
                    "history": _json_or_none(finding.history),
                    "introduced": _json_or_none(finding.introduced),
                    "fixed": _json_or_none(finding.fixed),
                    "effect": _json_or_none(finding.effect),
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
