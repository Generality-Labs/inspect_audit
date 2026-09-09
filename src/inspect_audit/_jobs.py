"""Remote work for the investigator: Hawk eval-set jobs, a ledger, and reservations.

The investigator's shell lives in a container with no credentials. Everything here runs
in the trusted Inspect process on the host, using the operator's Hawk login (the `hawk`
CLI and its keyring) and the operator's AWS credentials for staging input logs. Job
state is a JSON ledger in the investigation directory so a restarted session sees what
was already submitted instead of launching it again.
"""

import fcntl
import json
import re
import shutil
import subprocess
import time
from collections.abc import Iterator
from contextlib import contextmanager
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
    estimated_usd: float  # what the agent expected; kept to compare against reality
    reserved_usd: float = 0.0  # what the config can cost at worst; held until collected
    # pending: written before the CLI call, so a lost response is recoverable.
    # failed: the submission never reached Hawk; its reservation is released.
    status: str = "pending"
    actual_usd: float | None = None
    collected_to: str | None = None
    note: str = ""
    evals: list[dict[str, Any]] = field(default_factory=list)


class JobLedger:
    """Every remote job this investigation launched, on disk, under a file lock.

    Reservations are checked and written inside the same locked transaction, so two
    submissions running at once cannot both fit into the last of the allowance.
    """

    def __init__(self, root: Path) -> None:
        self.path = root / "jobs.json"
        self.lock_path = root / "jobs.lock"
        self.jobs: list[Job] = []
        self.reload()

    def reload(self) -> None:
        self.jobs = (
            [Job(**j) for j in json.loads(self.path.read_text())]
            if self.path.is_file()
            else []
        )

    @contextmanager
    def transaction(self) -> Iterator["JobLedger"]:
        """Exclusive access: re-read from disk, yield, write back on a clean exit."""
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.lock_path, "w") as handle:
            fcntl.flock(handle, fcntl.LOCK_EX)
            try:
                self.reload()
                yield self
                self.save()
            finally:
                fcntl.flock(handle, fcntl.LOCK_UN)

    def save(self) -> None:
        self.path.write_text(json.dumps([asdict(j) for j in self.jobs], indent=2))

    def get(self, label: str) -> Job | None:
        return next((j for j in self.jobs if j.label == label), None)

    def add(self, job: Job) -> None:
        if self.get(job.label) is not None:
            raise ValueError(f"a job labelled {job.label!r} already exists")
        self.jobs.append(job)

    def reserved_usd(self) -> float:
        """Money held against jobs that are alive and whose real cost is not yet known."""
        return sum(
            j.reserved_usd
            for j in self.jobs
            if j.actual_usd is None and j.status != "failed"
        )

    def actual_usd(self) -> float:
        return sum(j.actual_usd or 0.0 for j in self.jobs)

    def unpriced(self) -> list[str]:
        """Collected jobs whose cost could not be computed; their reservation stands."""
        return [
            j.label
            for j in self.jobs
            if j.actual_usd is None and j.collected_to is not None
        ]


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

    def eval_set_exists(self, eval_set_id: str) -> bool:
        """Whether Hawk has this eval set, used to resolve a submission with no answer."""
        out = self._run("list", "eval-sets", "--search", eval_set_id, "--limit", "50", timeout=120)
        return eval_set_id in out

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

    def watch(self, eval_set_id: str) -> str:
        """One-shot live status: per-task and per-sample phase, retries, limits, trouble."""
        out = self._run("watch", eval_set_id, "--no-follow", timeout=180)
        return out[-8000:]

    def status(self, eval_set_id: str) -> str:
        """The raw monitoring report: pod status, metrics, recent logs, as JSON."""
        out = self._run("status", eval_set_id, timeout=300)
        return out[-8000:]

    def trace(self, eval_set_id: str, lines: int = 100) -> str:
        """Runner's in-flight actions. An `enter` with no `exit` is what is hanging now."""
        out = self._run("trace", eval_set_id, "-n", str(lines), timeout=180)
        return out[-8000:]

    def stacktrace(self, eval_set_id: str) -> str:
        """py-spy dump of the live runner's thread stacks; running pod only."""
        out = self._run("stacktrace", eval_set_id, timeout=300)
        return out[-8000:]

    def transcript(self, sample_uuid: str, out_dir: Path) -> Path:
        """One sample's transcript as markdown, written to a file rather than returned."""
        out_dir.mkdir(parents=True, exist_ok=True)
        text = self._run("transcript", sample_uuid, timeout=600)
        path = out_dir / f"{sample_uuid}.md"
        path.write_text(text)
        return path

    def transcripts(self, eval_set_id: str, out_dir: Path, limit: int | None = None) -> list[Path]:
        """Every sample's transcript in the set, written to out_dir."""
        out_dir.mkdir(parents=True, exist_ok=True)
        args = ["transcripts", eval_set_id, "--output-dir", str(out_dir)]
        if limit is not None:
            args += ["--limit", str(limit)]
        self._run(*args, timeout=1800)
        return sorted(p for p in out_dir.iterdir() if p.is_file())


def stage_logs_to_s3(
    local_dir: Path, bucket: str, eval_set_id: str, profile: str | None
) -> str:
    """Stage the supplied logs once, under an eval-set prefix of their own.

    Jobs read them back through the Hawk API (`hawk:<id>/inputs/logs`), which is not
    scoped to the reading job's own prefix, so every child job can use this one copy
    whatever its own eval set id is. Returns the `hawk:` source to pass as `logs`.
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
    on spend-shaped fields, and no unknown keys at any level.
    """

    packages: list[str]  # exact git/pip specs allowed in packages: and tasks[].package
    task_names: list[str]  # registry package names allowed in tasks[].name
    models: list[str]  # OpenRouter model ids allowed anywhere a model is named
    auditor_images: list[str]  # every image any task argument may name
    hawk_api_url: str
    secrets: tuple[str, ...] = ("OPENROUTER_API_KEY",)
    env_keys: tuple[str, ...] = ("HAWK_API_URL", "HAWK_RUNNER_REFRESH_URL")
    max_limit: int = 1000
    max_epochs: int = 5
    max_token_limit: int = 10_000_000
    max_time_limit: int = 14_400
    # dollars per sample, enforced by the runner through Inspect's cost limit. The
    # reservation a job holds is this multiplied by the samples the config asks for.
    max_cost_limit_usd: float = 5.0
    max_worst_case_usd: float = 200.0
    # knobs that multiply the work or the request rate. Retries repeat a failed task,
    # so they multiply the reservation as well as being capped here.
    max_retry_attempts: int = 3
    max_message_limit: int = 10_000
    max_connections: int = 50
    max_retries: int = 20
    id_prefix: str = "inv-"


ALLOWED_TOP_LEVEL = {
    "name", "eval_set_id", "packages", "tasks", "models", "model_roles", "runner", "limit",
    "sample_shuffle", "epochs", "token_limit", "time_limit", "message_limit", "working_limit",
    "cost_limit", "max_connections", "max_retries", "retry_attempts", "timeout", "metadata",
    "tags", "log_images", "score",
}
ALLOWED_RUNNER = {"environment", "secrets"}
ALLOWED_MODEL_ARGS = {"base_url", "config"}
ALLOWED_MODEL_CONFIG = {"reasoning_effort", "max_tokens", "temperature", "reasoning_tokens"}
# task argument names that decide what runs, what it costs, or what it can reach.
# Anything matching is checked against the policy; everything else is the task's own
# parameter, executed by code that is already allowlisted.
# Substring, not suffix: `model_name`, `judge`, `image_uri` and `num_samples` all decide
# what runs or what it costs, and an allowlist keyed on exact spellings is walked around
# by renaming the argument. Anything that mentions one of these concepts is checked.
MODEL_ARG = re.compile(r"model|judge|grader|scorer|extractor|llm")
IMAGE_ARG = re.compile(r"image|container|registry")
SIZE_ARG = re.compile(r"^(n|count|limit|epochs)$|limit|sample|epoch|item|batch|repeat|attempt")
LOG_ARG = re.compile(r"^logs?$|log_dir|log_file|transcript")
FORBIDDEN_ARGS = {
    "setup", "sandbox", "sandboxes", "solver", "agent", "approval", "secrets", "secret",
    "env", "environment", "command", "entrypoint", "token", "key", "api_key",
    "credentials", "aws_profile", "bucket",
}


def parse_config(config: dict[str, Any]) -> tuple[Any, list[str]]:
    """Parse with Hawk's own schema, so the effective settings are what we check.

    Returns the parsed EvalSetConfig, or the schema's complaints. Hawk allows extra
    top-level keys, so this does not replace the allowlists; it resolves the shape.
    """
    try:
        from hawk.core.types.evals import EvalSetConfig
    except ImportError as ex:  # pragma: no cover - install-time problem, not a code path
        raise RuntimeError(
            "remote work needs Hawk's config schema: pip install 'inspect_audit[remote]'"
        ) from ex
    import pydantic

    try:
        return EvalSetConfig.model_validate(config), []
    except pydantic.ValidationError as ex:
        return None, [
            f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in ex.errors()[:12]
        ]


def _model_items(config: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    found: list[tuple[str, dict[str, Any]]] = []
    for group in config.get("models") or []:
        found.append((f"models[{group.get('name')}]", group))
    for role, group in (config.get("model_roles") or {}).items():
        found.append((f"model_roles.{role}", group))
    return found


def _task_arg_problems(
    where: str, args: dict[str, Any], policy: Policy, known_log_sources: set[str]
) -> list[str]:
    """The task arguments are the configuration that actually executes.

    Checked at every depth. Tasks pass nested mappings through to other constructors
    (inspect_audit's own `audit` task hands `task_args` straight to the audited task),
    so a rule applied only to the outer keys is a rule an inner key walks around: a
    grader model, an image or a size nested one level down would otherwise reach the
    runner unexamined.
    """
    problems: list[str] = []

    def walk(prefix: str, value: Any, depth: int = 0) -> None:
        if depth > 8:  # a config this deep is not a task argument
            problems.append(f"{where}: {prefix} is nested too deeply to check")
            return
        if isinstance(value, dict):
            for key, inner in value.items():
                check(f"{prefix}.{key}" if prefix else str(key), str(key), inner, depth)
            return
        if isinstance(value, list):
            for index, inner in enumerate(value):
                walk(f"{prefix}[{index}]", inner, depth + 1)
            return
        if isinstance(value, str):
            _string_problems(where, prefix, value, problems)

    def check(path: str, key: str, value: Any, depth: int) -> None:
        lowered = key.lower()
        if lowered in FORBIDDEN_ARGS:
            problems.append(f"{where}: task arg not allowed: {path!r}")
            return
        if MODEL_ARG.search(lowered):
            for model in value if isinstance(value, list) else [value]:
                if model is not None and model not in policy.models:
                    problems.append(f"{where}: {path}={model!r} is not an allowed model")
            return
        if IMAGE_ARG.search(lowered):
            if value is not None and value not in policy.auditor_images:
                problems.append(f"{where}: {path}={value!r} is not an allowed image")
            return
        if SIZE_ARG.search(lowered):
            if not isinstance(value, (int, bool)) or value is True or value is False:
                # a name-shaped size argument holding something else (a list of ids, a
                # path, a flag) is checked as whatever it is, not as a number
                walk(path, value, depth + 1)
                return
            cap = policy.max_epochs if "epoch" in lowered else policy.max_limit
            if value is not None and (
                not isinstance(value, int) or isinstance(value, bool) or not (0 < value <= cap)
            ):
                problems.append(f"{where}: {path}={value!r} must be a whole number from 1 to {cap}")
            return
        if LOG_ARG.search(lowered):
            for source in value if isinstance(value, list) else [value]:
                if not _known_log_source(str(source), known_log_sources):
                    problems.append(
                        f"{where}: {path}={source!r} must be a hawk: source this "
                        "investigation staged or ran"
                    )
            return
        walk(path, value, depth + 1)

    walk("", args)
    return problems


# a task argument may name data, a prompt or a flag; it may not name a location outside
# the job, because the runner holds credentials that reach some of those locations
_OUTSIDE = re.compile(r"^(s3://|gs://|hawk:|file://|https?://|ftp://|//)")
_ALLOWED_ABSOLUTE = ("/inputs/", "/workspace/", "/tmp/")


def _string_problems(where: str, path: str, text: str, problems: list[str]) -> None:
    if (
        _OUTSIDE.match(text)
        or text.startswith("~")
        or (text.startswith("/") and not text.startswith(_ALLOWED_ABSOLUTE))
    ):
        problems.append(
            f"{where}: {path} points outside this investigation: {text[:60]!r}"
        )


def _known_log_source(logs: str, known_log_sources: set[str]) -> bool:
    return (
        logs.startswith("hawk:")
        and logs.removeprefix("hawk:").split("/")[0] in known_log_sources
    )


def _samples_in(item: Any, config_limit: int | None) -> int | None:
    """How many samples one task item runs, or None when it does not say.

    Only `sample_ids` and the eval set's own `limit` are trusted. A task argument
    called `limit` is the task's business and may mean something else entirely, so
    believing it would let a job that runs a whole dataset reserve the price of one
    sample.
    """
    if item.sample_ids:
        return len(item.sample_ids)
    return config_limit


def worst_case_usd(parsed: Any, policy: Policy) -> float | None:
    """What a submitted config is expected to cost at its own stated ceiling.

    Hawk runs every task item against every model, `epochs` times, and re-runs a
    failed task up to `retry_attempts` times; the runner stops a sample once Inspect
    sees it pass `cost_limit` dollars. It is a stopping threshold rather than a hard
    ceiling, so the sample in flight can overshoot it, and a model the task builds for
    itself out of an unpriced name is not counted by it at all. Treat this as the
    number to hold against the allowance, not as a guarantee.

    None means the config does not bound itself, which is a refusal, not an unknown.
    """
    cost_limit = parsed.cost_limit
    if not isinstance(cost_limit, (int, float)) or cost_limit <= 0:
        return None
    limit = parsed.limit if isinstance(parsed.limit, int) and parsed.limit > 0 else None
    epochs = parsed.epochs
    epochs = epochs if isinstance(epochs, int) else getattr(epochs, "epochs", 1) or 1
    if not isinstance(epochs, int) or epochs < 1:
        return None
    models = sum(len(group.items) for group in parsed.models or []) or 1
    attempts = 1 + max(0, parsed.retry_attempts or 0)
    samples = 0
    for task in parsed.tasks:
        for item in task.items:
            per_item = _samples_in(item, limit)
            if per_item is None or per_item < 1:
                return None
            samples += per_item
    return float(cost_limit) * samples * models * int(epochs) * attempts


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
    if "eval_set_id" in config:
        problems.append(
            "remove eval_set_id: a fresh one is assigned at submission. Reusing an id makes "
            "Hawk resume that eval set rather than run a new job, and `logs` reaches a "
            "staged prefix through the Hawk API whatever this job's id is"
        )
    for pkg in config.get("packages") or []:
        if pkg not in policy.packages:
            problems.append(f"package not allowed: {pkg!r}")
    if not config.get("tasks"):
        problems.append("tasks is required")
    if not config.get("models"):
        problems.append("models is required")

    # the raw-dict checks run before the parse, so a config that Hawk's schema also
    # dislikes still hears the policy's objection rather than only the schema's
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
    if config.get("secrets"):
        problems.append("top-level secrets are not allowed; runner.secrets holds the provider key")
    for where, group in _model_items(config):
        if group.get("package") != "openai" or group.get("name") != "openrouter":
            problems.append(f"{where}: models must use package openai, provider openrouter")
        for item in group.get("items") or []:
            if item.get("name") not in policy.models:
                problems.append(f"{where}: model not allowed: {item.get('name')!r}")
            args = item.get("args") or {}
            if args.get("base_url") != OPENROUTER_BASE_URL:
                problems.append(f"{where}: args.base_url must be {OPENROUTER_BASE_URL}")
            extra = set(args) - ALLOWED_MODEL_ARGS
            if extra:
                problems.append(f"{where}: model args not allowed: {sorted(extra)}")
            cfg_extra = set(args.get("config") or {}) - ALLOWED_MODEL_CONFIG
            if cfg_extra:
                problems.append(f"{where}: model config keys not allowed: {sorted(cfg_extra)}")

    parsed, schema_problems = parse_config(config)
    if schema_problems:
        return problems + [f"Hawk rejects this config: {p}" for p in schema_problems]

    for task in parsed.tasks:
        if task.package not in policy.packages:
            problems.append(f"task package not allowed: {task.package!r}")
        if task.name not in policy.task_names:
            problems.append(f"task registry package not allowed: {task.name!r}")
        for item in task.items:
            where = f"tasks[{task.name}].{item.name}"
            if item.secrets:
                problems.append(f"{where}: task-level secrets are not allowed")
            if item.isolation is not None:
                problems.append(f"{where}: isolation is the operator's to set")
            if item.sample_ids and len(item.sample_ids) > policy.max_limit:
                problems.append(f"{where}: {len(item.sample_ids)} sample_ids exceeds {policy.max_limit}")
            problems += _task_arg_problems(where, item.args or {}, policy, known_log_sources)

    epochs = parsed.epochs if isinstance(parsed.epochs, int) else getattr(parsed.epochs, "epochs", None)
    for key, value, cap in (
        ("epochs", epochs, policy.max_epochs),
        ("token_limit", parsed.token_limit, policy.max_token_limit),
        ("time_limit", parsed.time_limit, policy.max_time_limit),
    ):
        if value is None:
            problems.append(f"{key} is required")
        elif not isinstance(value, int) or value > cap:
            problems.append(f"{key} {value!r} must be an integer up to {cap}")
    if parsed.limit is not None and (
        not isinstance(parsed.limit, int) or not (0 < parsed.limit <= policy.max_limit)
    ):
        problems.append(
            f"limit {parsed.limit!r} must be a whole number from 1 to {policy.max_limit}; "
            "a range is not allowed"
        )
    for task in parsed.tasks:
        for item in task.items:
            if item.sample_ids is not None and not item.sample_ids:
                problems.append(f"tasks[{task.name}].{item.name}: sample_ids is empty")
    for key, cap in (
        ("retry_attempts", policy.max_retry_attempts),
        ("message_limit", policy.max_message_limit),
        ("working_limit", policy.max_time_limit),
        ("max_connections", policy.max_connections),
        ("max_retries", policy.max_retries),
        ("timeout", policy.max_time_limit),
    ):
        value = config.get(key)
        if value is None:
            continue
        if not isinstance(value, int) or isinstance(value, bool) or not (0 <= value <= cap):
            problems.append(f"{key} {value!r} must be a whole number up to {cap}")
    if parsed.cost_limit is None:
        problems.append(
            f"cost_limit is required: dollars per sample, up to {policy.max_cost_limit_usd}. "
            "It is what makes the job's spend bounded, and what your reservation is computed from"
        )
    elif not (0 < parsed.cost_limit <= policy.max_cost_limit_usd):
        problems.append(
            f"cost_limit {parsed.cost_limit} must be above 0 and at most {policy.max_cost_limit_usd}"
        )

    worst = worst_case_usd(parsed, policy)
    if worst is None and not problems:
        problems.append(
            "the job does not state its size: set limit, or sample_ids on every task item"
        )
    elif worst is not None and worst > policy.max_worst_case_usd:
        problems.append(
            f"worst case ${worst:,.2f} (cost_limit x samples x models x epochs) exceeds "
            f"the ${policy.max_worst_case_usd:,.2f} a single job may hold; run it in parts"
        )
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
