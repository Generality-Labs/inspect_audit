"""inspect-dataset: static scanners over the eval's dataset, one finding per row.

By default the dataset is read through the eval's task, so the scan sees the samples the eval
runs on. A pilot-config declaration with HuggingFace settings scans the HuggingFace dataset instead.
"""

from __future__ import annotations

import json
import os
import tempfile
import time
import tomllib
from collections.abc import Mapping, Sequence
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
    CommandResult,
    Context,
    ProducerError,
    eval_yaml,
    new_run_id,
    package_of,
    run_command,
    skip_run,
    slug,
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


DUMP_SCRIPT = Path(__file__).with_name("_dump_task_samples.py")
REPLAY_SCRIPT = Path(__file__).with_name("_replay_samples.py")
# names the dumped samples for the replay task; _replay_samples.SAMPLES_ENV must match
SAMPLES_ENV = "INSPECT_AUDIT_SAMPLES_FILE"


def eval_dependency_args(ie_root: Path, package: str) -> list[str]:
    """`--extra`/`--group` flags for the eval's own dependencies, where the checkout declares them."""
    path = ie_root / "pyproject.toml"
    if not path.is_file():
        return []
    data = tomllib.loads(path.read_text())
    args: list[str] = []
    if package in data.get("project", {}).get("optional-dependencies", {}):
        args += ["--extra", package]
    if package in data.get("dependency-groups", {}):
        args += ["--group", package]
    return args


def eval_env_dir(ctx: Context) -> Path:
    """The scratch environment task dumps run in: never the checkout's own `.venv`."""
    if ctx.producers.eval_env:
        return Path(ctx.producers.eval_env)
    cache = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache")
    return cache / "inspect_audit" / "eval-envs" / f"ie-{slug(str(ctx.ie_root.resolve()))}"


def _expand(prefix: Sequence[str], ie_root: Path, eval_deps: list[str]) -> list[str]:
    argv: list[str] = []
    for part in prefix:
        if part == "{eval_deps}":
            argv += eval_deps
        else:
            argv.append(part.replace("{ie_root}", str(ie_root)))
    return argv


def _output_tail(result: CommandResult) -> str:
    return (result.stderr or result.stdout)[-1500:]


def run(target: str, ctx: Context) -> Run:
    """Scan the eval's dataset with static scanners into a temporary directory, then parse it.

    A task scan first dumps the task's samples in the eval's environment, then scans a replay of
    them in inspect-dataset's, so neither sees the other's dependencies.
    """
    timestamp = utcnow()
    declared = ctx.config.for_eval(target).dataset
    yaml_data = eval_yaml(ctx.ie_root, target)
    path, options, examined = scan_arguments(target, yaml_data, declared)
    if path is None:
        reason = (
            "HuggingFace settings are declared in the pilot config but there is no dataset path: declare `path`, or add a huggingface asset to eval.yaml external_assets"
            if declared and declared.selects_hf
            else "no dataset path: none declared in the pilot config, no huggingface asset in eval.yaml external_assets, and no tasks in eval.yaml"
        )
        return skip_run(PRODUCER, target, ctx, reason, timestamp=timestamp)
    listed = yaml_tasks(yaml_data)
    if declared and declared.task and listed and declared.task not in listed:
        return skip_run(
            PRODUCER,
            target,
            ctx,
            f"declared task {declared.task!r} is not among eval.yaml's tasks ({', '.join(listed)})",
            timestamp=timestamp,
        )
    ctx.out_dir.mkdir(parents=True, exist_ok=True)
    scan_dir = Path(tempfile.mkdtemp(prefix="inspect_dataset_", dir=ctx.out_dir))
    inputs: dict[str, JsonValue] = {"scan_dir": str(scan_dir)}
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="inspect_dataset_samples_") as work:
        if examined["mode"] == "task":
            samples, meta_path = Path(work) / "samples.jsonl", Path(work) / "meta.json"
            dump_argv = [
                *_expand(
                    ctx.producers.dataset_dump,
                    ctx.ie_root,
                    eval_dependency_args(ctx.ie_root, package_of(target)),
                ),
                str(DUMP_SCRIPT),
                path,
                str(samples),
                str(meta_path),
            ]
            inputs["dump_argv"] = list(dump_argv)
            dump_env = {
                "UV_PROJECT_ENVIRONMENT": str(eval_env_dir(ctx)),
                "PYTHONDONTWRITEBYTECODE": "1",
            }
            try:
                dumped = run_command(
                    dump_argv, timeout=ctx.producers.timeout_s, cwd=ctx.ie_root, env=dump_env
                )
            except ProducerError as ex:
                return skip_run(PRODUCER, target, ctx, str(ex), timestamp=timestamp)
            if dumped.returncode != 0 or not meta_path.is_file():
                return skip_run(
                    PRODUCER,
                    target,
                    ctx,
                    f"sample dump exit {dumped.returncode}: {_output_tail(dumped)}",
                    timestamp=timestamp,
                )
            meta = json.loads(meta_path.read_text())
            meta = meta if isinstance(meta, dict) else {}
            # the dataset as Inspect records it for this task, not whichever asset eval.yaml lists first
            identity = meta.get("dataset_location") or meta.get("dataset_name") or path
            dataset: DatasetRef | None = DatasetRef(path=str(identity))
            examined = {**examined, "samples": meta.get("samples")}
            argv = [
                *ctx.producers.dataset_task,
                "scan",
                f"{REPLAY_SCRIPT}@replay_samples",
                "-o",
                str(scan_dir),
            ]
            scan_env: dict[str, str] | None = {SAMPLES_ENV: str(samples)}
        else:
            argv = [*ctx.producers.dataset, "scan", path, *options, "-o", str(scan_dir)]
            dataset = None
            scan_env = None
        inputs["argv"] = list(argv)
        inputs["dataset"] = examined
        try:
            result = run_command(argv, timeout=ctx.producers.timeout_s, cwd=scan_dir, env=scan_env)
        except ProducerError as ex:
            return skip_run(PRODUCER, target, ctx, str(ex), timestamp=timestamp)
    duration = time.monotonic() - started
    if result.returncode != 0 or not (scan_dir / "scan_summary.json").is_file():
        return skip_run(
            PRODUCER,
            target,
            ctx,
            f"inspect-dataset exit {result.returncode}: {_output_tail(result)}",
            timestamp=timestamp,
        )
    try:
        return parse(
            scan_dir,
            target,
            subject_for(target, ctx),
            timestamp=timestamp,
            duration_s=duration,
            inputs=inputs,
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
