"""inspect-dataset: static scanners over the eval's dataset, one finding per row.

By default the dataset is read through the eval's task, so the scan sees the samples the eval
runs on. A pilot-config declaration with HuggingFace settings scans the HuggingFace dataset instead.
"""

from __future__ import annotations

import json
import tempfile
import time
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path
from typing import Any

from pydantic import JsonValue

from ..config import DatasetConfig, FieldRole
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

SEVERITY: dict[str, Severity] = {"low": "none", "medium": "minor", "high": "major"}
_SUMMARY_CHARS = 200


def hf_asset(data: Mapping[str, Any]) -> str | None:
    """The first HuggingFace source named in eval.yaml's external_assets, if any."""
    for asset in data.get("external_assets", []) or []:
        if isinstance(asset, dict) and asset.get("type") == "huggingface" and asset.get("source"):
            return str(asset["source"])
    return None


_FIELD_OPTIONS: dict[FieldRole, str] = {
    "question": "--question-field",
    "answer": "--answer-field",
    "id": "--id-field",
}


def yaml_tasks(data: Mapping[str, Any]) -> list[str]:
    """Task names listed in eval.yaml, in order."""
    return [
        str(task["name"])
        for task in data.get("tasks", []) or []
        if isinstance(task, dict) and task.get("name")
    ]


def scan_arguments(
    target: str, yaml_data: Mapping[str, Any], declared: DatasetConfig | None
) -> tuple[str | None, list[str], dict[str, JsonValue]]:
    """Dataset path, extra `inspect-dataset scan` options, and a record of what will be examined.

    Without HuggingFace settings in the declaration, the path is the task spec
    `inspect_evals/<task>`: the declared task, or the first one in eval.yaml. inspect-dataset then
    loads the samples through the eval's own loader, with its split, config, revision and field
    mapping. With HuggingFace settings, the declaration wins where it speaks and eval.yaml's
    HuggingFace asset supplies the path otherwise. `declared` in the record says whether any
    declaration was consulted, so a consumer can tell an inferred scan from a configured one;
    `mode` says which kind of scan it was.
    """
    tasks = yaml_tasks(yaml_data)
    task = (declared.task if declared else None) or (tasks[0] if tasks else None)
    if task and not (declared and declared.selects_hf):
        namespace = target.rsplit("/", 1)[0] if "/" in target else "inspect_evals"
        spec = f"{namespace}/{task}"
        return (
            spec,
            [],
            {
                "path": spec,
                "config": None,
                "split": None,
                "revision": None,
                "fields": {},
                "declared": declared is not None,
                "mode": "task",
                "task": task,
            },
        )
    asset = hf_asset(yaml_data)
    path = (declared.path if declared and declared.path else None) or asset
    options: list[str] = []
    if declared:
        for option, value in (
            ("--config", declared.config),
            ("--split", declared.split),
            ("--revision", declared.revision),
        ):
            if value:
                options += [option, value]
        for role, name in declared.fields.items():
            options += [_FIELD_OPTIONS[role], name]
    examined: dict[str, JsonValue] = {
        "path": path,
        "config": declared.config if declared else None,
        "split": declared.split if declared else None,
        "revision": declared.revision if declared else None,
        "fields": {str(role): name for role, name in declared.fields.items()} if declared else {},
        "declared": declared is not None,
        "mode": "hf",
        "task": None,
    }
    return path, options, examined


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
    dataset: DatasetRef | None = None,
) -> Run:
    """An inspect-dataset output directory as a Run. Scanners named in the summary without a file still get an outcome.

    `dataset` replaces the one the summary describes. A task scan's summary names the task spec
    and a `train` split whatever the task loads, so the caller supplies the eval's dataset instead.
    """
    run_id = new_run_id(PRODUCER, target, timestamp)
    summary = json.loads((scan_dir / "scan_summary.json").read_text())
    version = str(summary.get("version") or "unknown")
    if dataset is None:
        dataset = DatasetRef(
            path=summary.get("dataset_name"),
            config=summary.get("config"),
            split=summary.get("split"),
            revision=summary.get("revision"),
        )
    subject = subject.model_copy(update={"dataset": dataset})
    outcomes: list[Outcome] = []
    findings: list[Finding] = []
    for scanner, counts in sorted((summary.get("by_scanner") or {}).items()):
        total = int((counts or {}).get("total", 0))
        outcomes.append(
            Outcome(rule=scanner, status="fail" if total else "pass", message=f"{total} finding(s)")
        )
        path = scan_dir / f"{scanner}.json"
        if not path.is_file():
            continue
        for row in _rows(path):
            sample_id = str(
                row.get("sample_id")
                if row.get("sample_id") is not None
                else row.get("sample_index")
            )
            primary = SampleLocation(
                role="primary", dataset=dataset.path or "", sample_id=sample_id
            )
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
        id=run_id,
        timestamp=timestamp,
        producer=PRODUCER,
        producer_version=version,
        subject=subject,
        duration_s=duration_s,
        inputs=dict(inputs or {}),
        outcomes=outcomes,
        findings=findings,
    )


def run(target: str, ctx: Context) -> Run:
    """Scan the eval's dataset with static scanners into a temporary directory, then parse it."""
    timestamp = utcnow()
    declared = ctx.config.for_eval(target).dataset
    yaml_data = eval_yaml(ctx.ie_root, target)
    path, options, examined = scan_arguments(target, yaml_data, declared)
    if path is None:
        return skip_run(
            PRODUCER,
            target,
            ctx,
            "no dataset path: none declared in the pilot config, no huggingface asset in eval.yaml external_assets, and no tasks in eval.yaml",
            timestamp=timestamp,
        )
    if examined["mode"] == "task":
        prefix = [
            part.replace("{ie_root}", str(ctx.ie_root)) for part in ctx.producers.dataset_task
        ]
        dataset: DatasetRef | None = DatasetRef(path=hf_asset(yaml_data) or path)
    else:
        prefix = list(ctx.producers.dataset)
        dataset = None
    ctx.out_dir.mkdir(parents=True, exist_ok=True)
    scan_dir = Path(tempfile.mkdtemp(prefix="inspect_dataset_", dir=ctx.out_dir))
    argv = [*prefix, "scan", path, *options, "-o", str(scan_dir)]
    started = time.monotonic()
    try:
        result = run_command(argv, timeout=ctx.producers.timeout_s)
    except ProducerError as ex:
        return skip_run(PRODUCER, target, ctx, str(ex), timestamp=timestamp)
    duration = time.monotonic() - started
    if result.returncode != 0 or not (scan_dir / "scan_summary.json").is_file():
        return skip_run(
            PRODUCER,
            target,
            ctx,
            f"inspect-dataset exit {result.returncode}: {(result.stderr or result.stdout)[-1500:]}",
            timestamp=timestamp,
        )
    try:
        return parse(
            scan_dir,
            target,
            subject_for(target, ctx),
            timestamp=timestamp,
            duration_s=duration,
            inputs={"argv": argv, "scan_dir": str(scan_dir), "dataset": examined},
            dataset=dataset,
        )
    except (
        Exception
    ) as ex:  # a producer whose output we cannot read is a skipped producer, not a dead sweep
        return skip_run(
            PRODUCER,
            target,
            ctx,
            f"could not parse {PRODUCER} output: {type(ex).__name__}: {ex}",
            timestamp=timestamp,
        )
