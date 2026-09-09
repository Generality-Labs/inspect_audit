"""Remote work for the investigator: Hawk eval-set jobs, a ledger, and reservations.

The investigator's shell lives in a container with no credentials. Everything here runs
in the trusted Inspect process on the host, using the operator's Hawk login (the `hawk`
CLI and its keyring) and the operator's AWS credentials for staging input logs. Job
state is a JSON ledger in the investigation directory so a restarted session sees what
was already submitted instead of launching it again.
"""

import json
import re
import shutil
import subprocess
import time
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from logging import getLogger
from pathlib import Path
from typing import Any

import yaml

logger = getLogger(__name__)

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
TERMINAL = {"success", "error", "cancelled"}


@dataclass
class Job:
    label: str
    kind: str  # benchmark | audit
    eval_set_id: str
    config_path: str
    submitted_at: str
    estimated_usd: float
    status: str = "submitted"  # submitted | running | success | error | cancelled | stopped
    actual_usd: float | None = None
    collected_to: str | None = None
    note: str = ""
    evals: list[dict[str, Any]] = field(default_factory=list)


class JobLedger:
    """Append-mostly record of every remote job this investigation launched."""

    def __init__(self, root: Path) -> None:
        self.path = root / "jobs.json"
        self.jobs: list[Job] = []
        if self.path.is_file():
            self.jobs = [Job(**j) for j in json.loads(self.path.read_text())]

    def save(self) -> None:
        self.path.write_text(json.dumps([asdict(j) for j in self.jobs], indent=2))

    def get(self, label: str) -> Job | None:
        return next((j for j in self.jobs if j.label == label), None)

    def add(self, job: Job) -> None:
        if self.get(job.label) is not None:
            raise ValueError(f"a job labelled {job.label!r} already exists")
        self.jobs.append(job)
        self.save()

    def reserved_usd(self) -> float:
        """Money promised to jobs whose real cost is not yet known."""
        return sum(j.estimated_usd for j in self.jobs if j.actual_usd is None)

    def actual_usd(self) -> float:
        return sum(j.actual_usd or 0.0 for j in self.jobs)


class Hawk:
    """Thin wrapper over the `hawk` CLI, which holds the operator's login."""

    def __init__(self, api_url: str, secrets_file: str | None, binary: str = "hawk") -> None:
        self.env = {"HAWK_API_URL": api_url}
        self.secrets_file = secrets_file
        self.binary = binary

    def _run(self, *args: str, timeout: int = 600) -> str:
        import os

        result = subprocess.run(
            [self.binary, *args],
            capture_output=True,
            text=True,
            timeout=timeout,
            env={**os.environ, **self.env},
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"hawk {' '.join(args[:2])} failed: {(result.stderr or result.stdout)[-1500:]}"
            )
        return result.stdout

    def submit(self, config_path: Path) -> str:
        args = ["eval-set", "run", str(config_path), "--skip-confirm", "--log-dir-allow-dirty"]
        if self.secrets_file:
            args += ["--secrets-file", self.secrets_file]
        out = self._run(*args)
        match = re.search(r"Eval set ID:\s*(\S+)", out)
        if not match:
            raise RuntimeError(f"could not find the eval set id in hawk's output:\n{out[-800:]}")
        return match.group(1)

    def evals(self, eval_set_id: str) -> list[dict[str, str]]:
        """Task, model, status and sample counts per eval, parsed from the CLI table."""
        out = self._run("list", "evals", eval_set_id, timeout=120)
        rows: list[dict[str, str]] = []
        for line in out.splitlines():
            parts = [p for p in re.split(r"\s{2,}", line.strip()) if p]
            if len(parts) >= 4 and re.fullmatch(r"\d+/\d+", parts[-1]):
                rows.append(
                    {"task": parts[0], "model": parts[1], "status": parts[2], "samples": parts[3]}
                )
        return rows

    def samples(self, eval_set_id: str, limit: int = 500) -> list[dict[str, Any]]:
        out = self._run("list", "samples", eval_set_id, "--json", "--limit", str(limit), timeout=180)
        start = out.find("[")
        return list(json.loads(out[start:])) if start >= 0 else []

    def download(self, eval_set_id: str, out_dir: Path) -> list[Path]:
        out_dir.mkdir(parents=True, exist_ok=True)
        self._run("download", eval_set_id, "--output-dir", str(out_dir), timeout=1800)
        return sorted(out_dir.rglob("*.eval"))

    def stop(self, eval_set_id: str) -> None:
        self._run("stop", eval_set_id, timeout=300)


def stage_logs_to_s3(
    local_dir: Path, bucket: str, eval_set_id: str, profile: str | None
) -> str:
    """Copy supplied logs into the child job's own S3 prefix.

    A Hawk runner's credentials reach only `evals/<its eval set id>/`, so logs an
    audit job must read are placed inside that prefix before the job is submitted,
    and the job pins its eval set id. Returns the `hawk:` source the audit reads.
    """
    import os

    prefix = f"evals/{eval_set_id}/inputs/logs"
    cmd = ["aws", "s3", "cp", "--recursive", "--only-show-errors", str(local_dir), f"s3://{bucket}/{prefix}/"]
    env = {k: v for k, v in os.environ.items() if not k.startswith("AWS_ACCESS") and k != "AWS_SECRET_ACCESS_KEY" and k != "AWS_API_KEY"} if profile else dict(os.environ)
    if profile:
        env["AWS_PROFILE"] = profile
    result = subprocess.run(cmd, capture_output=True, text=True, env=env, timeout=3600)
    if result.returncode != 0:
        raise RuntimeError(f"staging logs to S3 failed: {result.stderr[-1500:]}")
    return f"hawk:{eval_set_id}/inputs/logs"


def _model_item(spec: dict[str, Any]) -> dict[str, Any]:
    """One Hawk model item, routed straight to OpenRouter (no middleman)."""
    item: dict[str, Any] = {"name": spec["model"], "args": {"base_url": OPENROUTER_BASE_URL}}
    config = {k: v for k, v in spec.items() if k in ("reasoning_effort", "max_tokens", "temperature") and v is not None}
    if config:
        item["args"]["config"] = config
    return item


def benchmark_config(
    *,
    name: str,
    task_package: str,
    task_name: str,
    models: list[dict[str, Any]],
    task_args: dict[str, Any] | None,
    limit: int | None,
    sample_ids: list[str] | None,
    epochs: int,
    hawk_api_url: str,
    token_limit: int = 2_000_000,
    time_limit: int = 3600,
    max_connections: int = 10,
) -> dict[str, Any]:
    """An eval-set config that runs the audited benchmark itself on Hawk."""
    item: dict[str, Any] = {"name": task_name.split("/")[-1]}
    args = dict(task_args or {})
    if sample_ids:
        args["sample_id"] = sample_ids  # inspect's own selector, honoured by eval_set
    if args:
        item["args"] = args
    config: dict[str, Any] = {
        "name": name,
        "packages": [task_package],
        "tasks": [{"package": task_package, "name": task_name.split("/")[0], "items": [item]}],
        "models": [{"package": "openai", "name": "openrouter", "items": [_model_item(m) for m in models]}],
        "runner": {
            "environment": {"HAWK_API_URL": hawk_api_url, "HAWK_RUNNER_REFRESH_URL": ""},
            "secrets": [{"name": "OPENROUTER_API_KEY", "description": "direct provider access"}],
        },
        "epochs": epochs,
        "token_limit": token_limit,
        "time_limit": time_limit,
        "max_connections": max_connections,
        "max_retries": 10,
        "retry_attempts": 0,
    }
    if limit is not None:
        config["limit"] = limit
    return config


def audit_config(
    *,
    name: str,
    eval_set_id: str | None,
    audit_package: str,
    task_package: str,
    audited_task: str,
    logs_source: str,
    items: list[str],
    limit: int | None,
    sample_ids: list[str] | None,
    auditor: dict[str, Any],
    grader: dict[str, Any] | None,
    auditor_image: str,
    notes: str | None,
    hawk_api_url: str,
) -> dict[str, Any]:
    """An eval-set config that runs inspect_audit/audit on Hawk over recorded logs."""
    args: dict[str, Any] = {
        "task": audited_task,
        "logs": logs_source,
        "items": items,
        "auditor_image": auditor_image,
    }
    if limit is not None:
        args["limit"] = limit
    if sample_ids:
        args["samples"] = sample_ids
    if notes:
        args["notes"] = notes
    config: dict[str, Any] = {
        "name": name,
        "packages": [audit_package, task_package],
        "tasks": [{"package": audit_package, "name": "inspect_audit", "items": [{"name": "audit", "args": args}]}],
        "models": [{"package": "openai", "name": "openrouter", "items": [_model_item(auditor)]}],
        "runner": {
            "environment": {"HAWK_API_URL": hawk_api_url, "HAWK_RUNNER_REFRESH_URL": ""},
            "secrets": [{"name": "OPENROUTER_API_KEY", "description": "auditor model and replayed grader"}],
        },
        "epochs": 1,
        "token_limit": 4_000_000,
        "time_limit": 7200,
        "max_connections": 5,
        "max_retries": 10,
        "retry_attempts": 0,
        "timeout": 600,
    }
    if eval_set_id:
        config["eval_set_id"] = eval_set_id
    if grader:
        config["model_roles"] = {"grader": {"package": "openai", "name": "openrouter", "items": [_model_item(grader)]}}
    return config


def write_config(root: Path, label: str, config: dict[str, Any]) -> Path:
    path = root / "jobs" / f"{label}.eval-set.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(config, sort_keys=False))
    return path


def slug(text: str) -> str:
    """A Hawk-safe eval set id fragment: lowercase alphanumerics and hyphens."""
    return re.sub(r"-+", "-", re.sub(r"[^a-z0-9-]", "-", text.lower())).strip("-")[:30]


def usage_cost(logs: list[Path]) -> tuple[float | None, dict[str, dict[str, int]]]:
    """Total cost of a set of downloaded logs from Inspect's registered prices."""
    from inspect_ai.log import read_eval_log
    from inspect_ai.model._model_info import get_model_info

    total = 0.0
    priced = True
    usage: dict[str, dict[str, int]] = {}
    for path in logs:
        header = read_eval_log(str(path), header_only=True)
        for model, u in (header.stats.model_usage or {}).items():
            usage.setdefault(model, {"input": 0, "cache_read": 0, "output": 0})
            usage[model]["input"] += u.input_tokens or 0
            usage[model]["cache_read"] += u.input_tokens_cache_read or 0
            usage[model]["output"] += u.output_tokens or 0
            info = get_model_info(model)
            cost = info.cost if info else None
            if cost is None:
                priced = False
                continue
            total += (
                (u.input_tokens or 0) * (cost.input or 0)
                + (u.input_tokens_cache_read or 0) * (cost.input_cache_read or 0)
                + (u.input_tokens_cache_write or 0) * (cost.input_cache_write or 0)
                + (u.output_tokens or 0) * (cost.output or 0)
            ) / 1_000_000
    return (total if priced else None), usage


def wait_for(hawk: Hawk, eval_set_id: str, minutes: float, poll_seconds: float = 60) -> list[dict[str, str]]:
    """Poll until every eval in the set is terminal or the wait expires. No model calls."""
    deadline = time.monotonic() + minutes * 60
    rows: list[dict[str, str]] = []
    while True:
        rows = hawk.evals(eval_set_id)
        if rows and all(r["status"] in TERMINAL for r in rows):
            return rows
        if time.monotonic() >= deadline:
            return rows
        time.sleep(poll_seconds)


def copy_into_inputs(files: list[Path], inputs: Path, label: str) -> Path:
    """Place collected logs under inputs/jobs/<label>/ so the container sees them read-only."""
    dest = inputs / "jobs" / label
    dest.mkdir(parents=True, exist_ok=True)
    for f in files:
        shutil.copyfile(f, dest / f.name)
    return dest


def utcnow() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")
