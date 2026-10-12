"""Runs as JSON files, findings as dataframes and parquet."""

from __future__ import annotations

import io
import json
from collections.abc import Sequence
from typing import TYPE_CHECKING, Any

import pandas as pd

from .fs import StoreFS
from .models import Run

if TYPE_CHECKING:
    from datetime import datetime

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

RUN_SUFFIX = ".run.json"

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


def write_run(run: Run, fs: StoreFS, key: str) -> str:
    """Write one run as indented JSON at `key`, creating parents."""
    return fs.write_text(key, run.model_dump_json(indent=1) + "\n")


def read_run(fs: StoreFS, key: str) -> Run:
    return Run.model_validate_json(fs.read_text(key))


def read_runs(fs: StoreFS) -> list[Run]:
    """Every run under `<slug>/runs/`, sorted by key: the whole history, not just the current view."""
    return [read_run(fs, key) for key in fs.glob(f"*/runs/*{RUN_SUFFIX}")]


def read_current(fs: StoreFS) -> list[Run]:
    """The current view: per eval and producer, the newest run by timestamp, then run id.

    Derived rather than recorded, so two writers appending runs at once never contend for a
    shared manifest. Ties on timestamp go to the later run id, so a rerun written in the same
    second (its file carries a `-2` suffix) is the current one.
    """
    by_eval_dir: dict[str, list[str]] = {}
    for key in fs.glob(f"*/runs/*{RUN_SUFFIX}"):
        by_eval_dir.setdefault(key.split("/", 1)[0], []).append(key)
    current: list[Run] = []
    for eval_dir in sorted(by_eval_dir):
        newest: dict[str, tuple[tuple[datetime, str], Run]] = {}
        for key in by_eval_dir[eval_dir]:
            run = read_run(fs, key)
            order = (run.timestamp, key.rsplit("/", 1)[-1].removesuffix(RUN_SUFFIX))
            if run.producer not in newest or order > newest[run.producer][0]:
                newest[run.producer] = (order, run)
        current.extend(newest[producer][1] for producer in sorted(newest))
    return current


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


def write_parquet(frame: pd.DataFrame, fs: StoreFS, key: str) -> str:
    buffer = io.BytesIO()
    frame.to_parquet(buffer, index=False)
    return fs.write_bytes(key, buffer.getvalue())
