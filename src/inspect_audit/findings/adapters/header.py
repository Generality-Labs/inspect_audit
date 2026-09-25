"""Facts a log header settles without a model: sample counts, drift, unscored samples, dirty revisions, unknown ids."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any

from inspect_ai.log import EvalLog, read_eval_log
from pydantic import JsonValue

from ..fingerprint import FINGERPRINT_VERSION, fingerprint
from ..models import (
    AnyLocation,
    Dimension,
    Finding,
    LogLocation,
    Outcome,
    Run,
    ScorerLocation,
    Severity,
    Source,
    Subject,
    utcnow,
)
from . import Context, eval_yaml, new_run_id, package_of, skip_run, subject_for

PRODUCER = "inspect_audit_header"

_RULES: dict[str, tuple[Dimension, Severity]] = {
    "header.dataset_samples": ("dataset", "minor"),
    "header.version_drift": ("informativeness", "minor"),
    "header.unscored_samples": ("grading", "minor"),
    "header.dirty_revision": ("informativeness", "none"),
    "header.unknown_sample_ids": ("dataset", "major"),
}


def eval_spec_dict(log: EvalLog) -> dict[str, JsonValue]:
    """The header's `eval` block as JSON, minus the potentially huge sample id list."""
    spec: dict[str, Any] = log.eval.model_dump(mode="json", exclude_none=True)
    dataset = spec.get("dataset")
    if isinstance(dataset, dict):
        dataset.pop("sample_ids", None)
    return spec


def _tail(name: str | None) -> str | None:
    return name.rsplit("/", 1)[-1] if name else None


def task_names(yaml_data: Mapping[str, Any], target: str) -> set[str]:
    """The unqualified task names a target answers to: its package name plus every `tasks[].name` in eval.yaml."""
    names = {package_of(target)}
    tasks = yaml_data.get("tasks")
    for entry in tasks if isinstance(tasks, list) else []:
        if isinstance(entry, dict) and entry.get("name"):
            names.add(str(entry["name"]))
    return names


def _header_task(header: EvalLog) -> str | None:
    return _tail(header.eval.task_registry_name) or _tail(header.eval.task)


def matching_headers(logs: Sequence[Path], target: str, names: set[str] | None = None) -> list[tuple[Path, EvalLog]]:
    """Headers whose registry name or task name is one of `names` (default: the target's package name).

    Multi-task packages such as lab_bench have no task named after the package, so callers pass the
    names from eval.yaml via `task_names`.
    """
    wanted = names or {package_of(target)}
    matched: list[tuple[Path, EvalLog]] = []
    for path in logs:
        try:
            header = read_eval_log(str(path), header_only=True)
        except Exception:  # an unreadable log is not this eval's problem
            continue
        if wanted & {_tail(header.eval.task_registry_name), _tail(header.eval.task)}:
            matched.append((path, header))
    return matched


def _declared_samples(yaml_data: Mapping[str, Any], task_name: str | None) -> int | None:
    """`dataset_samples` for the eval.yaml task entry named `task_name`."""
    tasks = yaml_data.get("tasks")
    for entry in tasks if isinstance(tasks, list) else []:
        if isinstance(entry, dict) and entry.get("name") == task_name and isinstance(entry.get("dataset_samples"), int):
            return int(entry["dataset_samples"])
    return None


class _Builder:
    def __init__(self, target: str, subject: Subject, run_id: str) -> None:
        self.target, self.subject, self.run_id = target, subject, run_id
        self.outcomes: list[Outcome] = []
        self.findings: list[Finding] = []

    def outcome(self, rule: str, fired: bool, message: str | None = None) -> None:
        self.outcomes.append(Outcome(rule=rule, status="fail" if fired else "pass", message=message))

    def finding(self, rule: str, summary: str, locations: list[AnyLocation], header: EvalLog) -> None:
        dimension, severity = _RULES[rule]
        primary = next(location for location in locations if location.role == "primary")
        self.findings.append(
            Finding(
                fingerprint=fingerprint(PRODUCER, rule, self.target, primary),
                fingerprint_version=FINGERPRINT_VERSION,
                producer=PRODUCER,
                rule=rule,
                subject=self.subject,
                dimension=dimension,
                severity=severity,
                status="supported",
                summary=summary,
                locations=locations,
                run_id=self.run_id,
                source=Source(format="inspect_ai.log.EvalSpec", record=None, eval_spec=eval_spec_dict(header)),
            )
        )


def parse(
    headers: Sequence[tuple[Path, EvalLog]],
    target: str,
    subject: Subject,
    yaml_data: Mapping[str, Any],
    *,
    timestamp: datetime,
    resolved_ids: set[str] | None = None,
) -> Run:
    """Run the header checks over already-read headers."""
    run_id = new_run_id(PRODUCER, target, timestamp)
    build = _Builder(target, subject, run_id)

    fired = False
    any_declared = False
    for path, header in headers:
        declared = _declared_samples(yaml_data, _header_task(header))
        any_declared = any_declared or declared is not None
        actual = header.eval.dataset.samples
        if declared is not None and actual is not None and actual != declared:
            fired = True
            build.finding(
                "header.dataset_samples",
                f"log records {actual} dataset samples; eval.yaml declares {declared}",
                [LogLocation(role="primary", eval_id=header.eval.eval_id, path="eval.dataset.samples", location_hint=str(path), quote=str(actual))],
                header,
            )
    build.outcome("header.dataset_samples", fired, None if any_declared else "eval.yaml declares no dataset_samples for these tasks")

    versions = {(str(h.eval.task_version), (h.eval.packages or {}).get("inspect_evals")) for _, h in headers}
    if len(versions) > 1:
        newest = max(headers, key=lambda pair: str(pair[1].eval.created))
        locations: list[AnyLocation] = [
            LogLocation(
                role="primary" if header is newest[1] else "related",
                eval_id=header.eval.eval_id,
                path="eval.task_version",
                location_hint=str(path),
                quote=f"{header.eval.task_version} / {(header.eval.packages or {}).get('inspect_evals')}",
            )
            for path, header in headers
        ]
        build.finding(
            "header.version_drift",
            f"{len(headers)} logs span {len(versions)} distinct task or package versions",
            locations,
            newest[1],
        )
    build.outcome("header.version_drift", len(versions) > 1)

    unscored_fired = False
    for _path, header in headers:
        total = header.results.total_samples if header.results else None
        for score in header.results.scores if header.results else []:
            if score.unscored_samples:
                unscored_fired = True
                build.finding(
                    "header.unscored_samples",
                    f"scorer {score.name} left {score.unscored_samples} of {total if total is not None else '?'} samples unscored",
                    [ScorerLocation(role="primary", eval_id=header.eval.eval_id, scorer=score.name, quote=str(score.unscored_samples))],
                    header,
                )
    build.outcome("header.unscored_samples", unscored_fired)

    dirty_fired = False
    for path, header in headers:
        if header.eval.revision is not None and header.eval.revision.dirty:
            dirty_fired = True
            build.finding(
                "header.dirty_revision",
                f"log was produced from a dirty checkout of {header.eval.revision.origin} at {header.eval.revision.commit}",
                [LogLocation(role="primary", eval_id=header.eval.eval_id, path="eval.revision.dirty", location_hint=str(path), quote="true")],
                header,
            )
    build.outcome("header.dirty_revision", dirty_fired)

    if resolved_ids is not None:
        unknown_fired = False
        for path, header in headers:
            logged = [str(i) for i in (header.eval.dataset.sample_ids or [])]
            missing = [i for i in logged if i not in resolved_ids]
            if missing:
                unknown_fired = True
                build.finding(
                    "header.unknown_sample_ids",
                    f"{len(missing)} of {len(logged)} logged sample ids are not in the resolved dataset (e.g. {missing[0]})",
                    [LogLocation(role="primary", eval_id=header.eval.eval_id, path="eval.dataset.sample_ids", location_hint=str(path))],
                    header,
                )
        build.outcome("header.unknown_sample_ids", unknown_fired)

    return Run(
        id=run_id, timestamp=timestamp, producer=PRODUCER, subject=subject,
        inputs={"logs": [str(path) for path, _ in headers]}, outcomes=build.outcomes, findings=build.findings,
    )


def _resolved_ids(target: str, headers: Sequence[tuple[Path, EvalLog]]) -> tuple[set[str] | None, str | None]:
    """The resolved dataset's sample ids, or None with the reason they cannot be compared."""
    from inspect_audit._resolve import resolve_task

    task_args = dict(headers[0][1].eval.task_args or {}) if headers else {}
    try:
        task = resolve_task(target, task_args)
        ids = [sample.id for sample in task.dataset]
    except Exception as ex:
        return None, f"could not resolve the task to compare sample ids: {type(ex).__name__}: {ex}"
    if any(i is None for i in ids):
        return None, "the resolved dataset has samples with no ids; Inspect assigns them at run time, so logged ids cannot be compared"
    return {str(i) for i in ids}, None


def run(target: str, ctx: Context) -> Run:
    """Read the headers of the logs that match `target` and run the checks."""
    timestamp = utcnow()
    yaml_data = eval_yaml(ctx.ie_root, target)
    headers = matching_headers(ctx.logs, target, task_names(yaml_data, target))
    if not headers:
        return skip_run(PRODUCER, target, ctx, f"no logs for target {target} among {len(ctx.logs)} file(s)", timestamp=timestamp)
    resolved, reason = _resolved_ids(target, headers) if ctx.resolve else (None, None)
    result = parse(headers, target, subject_for(target, ctx), yaml_data, timestamp=timestamp, resolved_ids=resolved)
    if ctx.resolve and resolved is None:
        result.outcomes.append(Outcome(rule="header.unknown_sample_ids", status="skip", message=reason))
    return result
