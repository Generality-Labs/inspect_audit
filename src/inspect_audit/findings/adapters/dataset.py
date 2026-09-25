"""inspect-dataset: static scanners over the eval's HuggingFace dataset, one finding per row."""

from __future__ import annotations

import json
import tempfile
import time
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path
from typing import Any

from ..fingerprint import FINGERPRINT_VERSION, fingerprint
from ..models import (
    DatasetRef,
    Finding,
    Outcome,
    Run,
    SampleLocation,
    Severity,
    Source,
    Subject,
    utcnow,
)
from . import (
    Context,
    ProducerError,
    eval_yaml,
    new_run_id,
    run_command,
    skip_run,
    subject_for,
)

PRODUCER = "inspect_dataset"

# per-eval scan arguments where auto-detection does not work. Keys are CLI option names without dashes.
DATASET_OVERRIDES: dict[str, dict[str, str]] = {
    "inspect_evals/stereoset": {
        "config": "intersentence",
        "split": "validation",
        "question_field": "context",
        "answer_field": "sentences",
        "id_field": "id",
    },
}

SEVERITY: dict[str, Severity] = {"low": "none", "medium": "minor", "high": "major"}
_SUMMARY_CHARS = 200


def hf_asset(data: Mapping[str, Any]) -> str | None:
    """The first HuggingFace source named in eval.yaml's external_assets, if any."""
    for asset in data.get("external_assets", []) or []:
        if isinstance(asset, dict) and asset.get("type") == "huggingface" and asset.get("source"):
            return str(asset["source"])
    return None


def _rows(path: Path) -> list[dict[str, Any]]:
    loaded = json.loads(path.read_text())
    rows = loaded.get("findings", []) if isinstance(loaded, dict) else loaded
    return [row for row in rows if isinstance(row, dict)]


def parse(
    scan_dir: Path,
    target: str,
    subject: Subject,
    *,
    timestamp: datetime,
    duration_s: float | None = None,
    inputs: Mapping[str, Any] | None = None,
) -> Run:
    """An inspect-dataset output directory as a Run. Scanners named in the summary without a file still get an outcome."""
    run_id = new_run_id(PRODUCER, target, timestamp)
    summary = json.loads((scan_dir / "scan_summary.json").read_text())
    version = str(summary.get("version") or "unknown")
    dataset = DatasetRef(
        path=summary.get("dataset_name"), config=summary.get("config"), split=summary.get("split"), revision=summary.get("revision")
    )
    subject = subject.model_copy(update={"dataset": dataset})
    outcomes: list[Outcome] = []
    findings: list[Finding] = []
    for scanner, counts in sorted((summary.get("by_scanner") or {}).items()):
        total = int((counts or {}).get("total", 0))
        outcomes.append(Outcome(rule=scanner, status="fail" if total else "pass", message=f"{total} finding(s)"))
        path = scan_dir / f"{scanner}.json"
        if not path.is_file():
            continue
        for row in _rows(path):
            sample_id = str(row.get("sample_id") if row.get("sample_id") is not None else row.get("sample_index"))
            primary = SampleLocation(role="primary", dataset=dataset.path or "", sample_id=sample_id)
            explanation = str(row.get("explanation") or scanner)
            findings.append(
                Finding(
                    fingerprint=fingerprint(PRODUCER, scanner, target, primary),
                    fingerprint_version=FINGERPRINT_VERSION,
                    producer=PRODUCER,
                    rule=scanner,
                    subject=subject,
                    dimension="dataset",
                    severity=SEVERITY.get(str(row.get("severity")), "minor"),
                    status="supported",
                    summary=explanation[:_SUMMARY_CHARS],
                    locations=[primary],
                    run_id=run_id,
                    source=Source(format=f"inspect_dataset.Finding@{version}", record=dict(row)),
                )
            )
    return Run(
        id=run_id, timestamp=timestamp, producer=PRODUCER, producer_version=version, subject=subject,
        duration_s=duration_s, inputs=dict(inputs or {}), outcomes=outcomes, findings=findings,
    )


def run(target: str, ctx: Context) -> Run:
    """Scan the eval's HuggingFace dataset with static scanners into a temporary directory, then parse it."""
    timestamp = utcnow()
    source = hf_asset(eval_yaml(ctx.ie_root, target))
    if source is None:
        return skip_run(PRODUCER, target, ctx, "no huggingface asset in eval.yaml external_assets", timestamp=timestamp)
    overrides = DATASET_OVERRIDES.get(target, {})
    ctx.out_dir.mkdir(parents=True, exist_ok=True)
    scan_dir = Path(tempfile.mkdtemp(prefix="inspect_dataset_", dir=ctx.out_dir))
    argv = [*ctx.producers.dataset, "scan", source]
    for key, value in overrides.items():
        argv += [f"--{key.replace('_', '-')}", value]
    argv += ["-o", str(scan_dir)]
    started = time.monotonic()
    try:
        result = run_command(argv, timeout=ctx.producers.timeout_s)
    except ProducerError as ex:
        return skip_run(PRODUCER, target, ctx, str(ex), timestamp=timestamp)
    duration = time.monotonic() - started
    if result.returncode != 0 or not (scan_dir / "scan_summary.json").is_file():
        return skip_run(
            PRODUCER, target, ctx,
            f"inspect-dataset exit {result.returncode}: {(result.stderr or result.stdout)[-1500:]}", timestamp=timestamp,
        )
    try:
        return parse(
            scan_dir, target, subject_for(target, ctx), timestamp=timestamp, duration_s=duration,
            inputs={"argv": argv, "scan_dir": str(scan_dir)},
        )
    except Exception as ex:  # a producer whose output we cannot read is a skipped producer, not a dead sweep
        return skip_run(PRODUCER, target, ctx, f"could not parse {PRODUCER} output: {type(ex).__name__}: {ex}", timestamp=timestamp)
