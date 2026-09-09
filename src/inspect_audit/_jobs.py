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

    def logs(self, eval_set_id: str, lines: int = 120) -> str:
        """Tail of the runner's own log, the place install failures and crashes show up."""
        out = self._run("logs", eval_set_id, "-n", str(lines), timeout=120)
        return out[-6000:]


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


@dataclass
class Policy:
    """What a submitted eval-set config may contain. Enforced in code, not prompt.

    A Hawk eval-set config runs arbitrary Python from `packages:` inside a runner that
    holds the operator's provider key and S3 credentials, so the config is treated as
    hostile input: allowlists for everything that names code or credentials, hard caps
    on spend-shaped fields, and no unknown keys.
    """

    packages: list[str]  # exact git/pip specs allowed in packages: and tasks[].package
    task_names: list[str]  # registry package names allowed in tasks[].name
    models: list[str]  # OpenRouter model ids allowed anywhere a model is named
    auditor_images: list[str]
    hawk_api_url: str
    secrets: tuple[str, ...] = ("OPENROUTER_API_KEY",)
    env_keys: tuple[str, ...] = ("HAWK_API_URL", "HAWK_RUNNER_REFRESH_URL")
    max_limit: int = 1000
    max_epochs: int = 5
    max_token_limit: int = 10_000_000
    max_time_limit: int = 14_400
    id_prefix: str = "inv-"


ALLOWED_TOP_LEVEL = {
    "name", "eval_set_id", "packages", "tasks", "models", "model_roles", "runner", "limit",
    "sample_shuffle", "epochs", "token_limit", "time_limit", "message_limit", "working_limit",
    "max_connections", "max_retries", "retry_attempts", "timeout", "metadata", "tags",
    "log_images", "score",
}
ALLOWED_RUNNER = {"environment", "secrets"}


def _model_items(config: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    found: list[tuple[str, dict[str, Any]]] = []
    for group in config.get("models") or []:
        found.append((f"models[{group.get('name')}]", group))
    for role, group in (config.get("model_roles") or {}).items():
        found.append((f"model_roles.{role}", group))
    return found


def validate_config(
    config: dict[str, Any], policy: Policy, known_log_sources: set[str]
) -> list[str]:
    """Every way the config could do something other than run an allowed eval, as a list.

    Empty list means submit. `known_log_sources` are the `hawk:` sources this
    investigation created (staged inputs, its own finished jobs).
    """
    problems: list[str] = []
    if not isinstance(config, dict):
        return ["config must be a mapping"]
    unknown = set(config) - ALLOWED_TOP_LEVEL
    if unknown:
        problems.append(f"keys not allowed: {sorted(unknown)}")
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{1,40}", str(config.get("name", ""))):
        problems.append("name must be lowercase letters, digits, hyphens (2-41 chars)")
    if "eval_set_id" in config and not str(config["eval_set_id"]).startswith(policy.id_prefix):
        problems.append(f"eval_set_id must start with {policy.id_prefix!r}")
    for pkg in config.get("packages") or []:
        if pkg not in policy.packages:
            problems.append(f"package not allowed: {pkg!r}")
    tasks = config.get("tasks") or []
    if not tasks:
        problems.append("tasks is required")
    for t in tasks:
        if t.get("package") not in policy.packages:
            problems.append(f"task package not allowed: {t.get('package')!r}")
        if t.get("name") not in policy.task_names:
            problems.append(f"task registry package not allowed: {t.get('name')!r}")
        for item in t.get("items") or []:
            args = item.get("args") or {}
            if "logs" in args:
                logs = str(args["logs"])
                if not logs.startswith("hawk:") or logs.split("/")[0].removeprefix("hawk:") not in known_log_sources:
                    problems.append(
                        f"logs must be a hawk: source this investigation staged or ran, not {logs!r}"
                    )
            if "auditor_image" in args and args["auditor_image"] not in policy.auditor_images:
                problems.append(f"auditor_image not allowed: {args['auditor_image']!r}")
            for forbidden in ("setup", "sandbox", "solver"):
                if forbidden in args:
                    problems.append(f"task arg not allowed: {forbidden!r}")
    if not config.get("models"):
        problems.append("models is required")
    for where, group in _model_items(config):
        if group.get("package") != "openai" or group.get("name") != "openrouter":
            problems.append(f"{where}: models must use package openai, provider openrouter")
        for item in group.get("items") or []:
            if item.get("name") not in policy.models:
                problems.append(f"{where}: model not allowed: {item.get('name')!r}")
            args = item.get("args") or {}
            if args.get("base_url") != OPENROUTER_BASE_URL:
                problems.append(f"{where}: args.base_url must be {OPENROUTER_BASE_URL}")
            extra = set(args) - {"base_url", "config"}
            if extra:
                problems.append(f"{where}: model args not allowed: {sorted(extra)}")
            cfg_extra = set(args.get("config") or {}) - {"reasoning_effort", "max_tokens", "temperature", "reasoning_tokens"}
            if cfg_extra:
                problems.append(f"{where}: model config keys not allowed: {sorted(cfg_extra)}")
    runner = config.get("runner") or {}
    if set(runner) - ALLOWED_RUNNER:
        problems.append(f"runner keys not allowed: {sorted(set(runner) - ALLOWED_RUNNER)} (no image, cpu, memory, cleanup)")
    env = runner.get("environment") or {}
    if set(env) - set(policy.env_keys):
        problems.append(f"runner.environment keys not allowed: {sorted(set(env) - set(policy.env_keys))}")
    if env.get("HAWK_RUNNER_REFRESH_URL", None) != "" or env.get("HAWK_API_URL") != policy.hawk_api_url:
        problems.append(
            f"runner.environment must set HAWK_API_URL to {policy.hawk_api_url} and HAWK_RUNNER_REFRESH_URL to ''"
        )
    for secret in runner.get("secrets") or []:
        if secret.get("name") not in policy.secrets or set(secret) - {"name", "description", "type"} or secret.get("type", "env") != "env":
            problems.append(f"runner secret not allowed: {secret}")
    for key, cap in (("limit", policy.max_limit), ("epochs", policy.max_epochs), ("token_limit", policy.max_token_limit), ("time_limit", policy.max_time_limit)):
        value = config.get(key)
        if key in ("token_limit", "time_limit", "epochs") and value is None:
            problems.append(f"{key} is required")
        if isinstance(value, int) and value > cap:
            problems.append(f"{key} {value} exceeds the cap {cap}")
        if value is not None and not isinstance(value, int):
            problems.append(f"{key} must be an integer")
    if "limit" not in config and not any(
        (item.get("args") or {}).get("sample_id") or (item.get("args") or {}).get("samples") or (item.get("args") or {}).get("limit")
        for t in tasks for item in t.get("items") or []
    ):
        problems.append("set limit, or select samples in the task args: every job states its size")
    return problems


def task_package_name(spec: str) -> str:
    """The registry name of a package from its git or pip spec.

    git+https://.../inspect_evals@sha -> inspect_evals; inspect-evals==1.0 -> inspect_evals.
    """
    tail = spec.split("#")[0].rstrip("/").split("/")[-1]
    tail = tail.split("@")[0].removesuffix(".git")
    tail = re.split(r"[=<>!~ ]", tail)[0]
    return tail.replace("-", "_")


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
