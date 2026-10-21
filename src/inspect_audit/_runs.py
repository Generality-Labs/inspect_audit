"""Where child eval sets run: one job description, two places to run it.

A job is an Inspect eval set: one task, its arguments, the samples, the models. The
investigator says what to run; a backend decides where. `HawkRuns` submits it
through Hawk's own client, as whoever this process is logged in as, so its model
calls bill to the same key as the investigator's. `LocalRuns` starts
`inspect eval-set` in a detached process on this machine, with this process's
environment and so its key. Both hand back finished logs as local files.
"""

import asyncio
import inspect
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, Protocol

# a job's lifecycle as both backends report it
RUNNING = "running"
COMPLETE = "complete"
FAILED = "failed"
STOPPED = "stopped"
# Hawk's own verdict that a job has stopped making progress: worth a person's look
STUCK = "stuck"
FINISHED = frozenset({COMPLETE, FAILED, STOPPED})


@dataclass
class Job:
    """One child eval set: what runs, never where."""

    label: str
    task: str  # registry name, e.g. inspect_evals/simpleqa_verified
    package: str  # pip spec that installs the task's package
    models: list[str]  # OpenRouter model ids, e.g. openai/gpt-5-mini
    task_args: dict[str, Any] = field(default_factory=dict)
    model_roles: dict[str, str] = field(default_factory=dict)
    sample_ids: list[str] | None = None
    # positions in the dataset, first to last counting from 1, inclusive: how a dataset
    # whose samples carry no ids is selected (Inspect filters ids before numbering them)
    sample_range: tuple[int, int] | None = None
    epochs: int = 1
    extra_packages: list[str] = field(default_factory=list)


class Runs(Protocol):
    """A place jobs run. Ids are the backend's own."""

    async def submit(self, job: Job) -> str: ...
    async def status(self, run_id: str) -> str: ...
    async def logs(self, run_id: str, out_dir: Path) -> list[Path]: ...
    async def runner_log(self, run_id: str) -> str: ...
    async def stop(self, run_id: str) -> None: ...


def hawk_config(job: Job, *, restricted: bool = False) -> dict[str, Any]:
    """The eval-set config Hawk runs for a job. Models route through Hawk's proxy."""
    package_name, _, task_name = job.task.partition("/")

    def group(model: str) -> dict[str, Any]:
        return {"package": "openai", "name": "openrouter", "items": [{"name": model}]}

    item: dict[str, Any] = {"name": task_name, "args": job.task_args}
    if job.sample_ids is not None:
        item["sample_ids"] = job.sample_ids
    limit = [job.sample_range[0] - 1, job.sample_range[1]] if job.sample_range else None
    config: dict[str, Any] = {
        "name": job.label,
        "limit": limit,
        "packages": [job.package, *job.extra_packages],
        "tasks": [{"package": job.package, "name": package_name, "items": [item]}],
        "models": [group(m) for m in job.models],
        "model_roles": {role: group(m) for role, m in job.model_roles.items()} or None,
        "epochs": job.epochs,
        # a failed child is reported, not retried behind the investigator's back
        "retry_attempts": 0,
    }
    if restricted:
        # Judges selected through task arguments are outside the eval model grid.
        judges = (job.task_args.get("judge") or {}) if job.task == "inspect_audit/audit" else {}
        if judges:
            config["additional_models"] = [
                group(str(m).removeprefix("openrouter/")) for m in dict.fromkeys(judges.values())
            ]
        sources = (job.task_args.get("logs") or []) if job.task == "inspect_audit/audit" else []
        if any(not str(source).startswith("hawk:") for source in sources):
            raise ValueError("restricted audit jobs require Hawk transcript sources")
        ids = list(dict.fromkeys(s.removeprefix("hawk:") for s in sources))
        if any(not sid or "," in sid for sid in ids):
            raise ValueError("declare each Hawk transcript source as a separate hawk:<id>")
        if ids:
            config["transcripts"] = {"sources": [{"eval_set_id": sid} for sid in ids]}
    return config


class HawkLogin:
    """Hawk credentials for whoever this process runs as.

    Inside a Hawk runner, the runner's own refresh hook. Elsewhere, the operator's
    `hawk` login: only the `hawk` binary that wrote it can read it from the keychain
    without asking, so that binary is asked, past the venv's copy `uv run` puts
    first on PATH.
    """

    def __init__(self, api_url: str) -> None:
        self.api_url = api_url
        venv = Path(sys.executable).with_name("hawk")
        others = [
            found
            for directory in os.environ.get("PATH", "").split(os.pathsep)
            if (found := shutil.which("hawk", path=directory))
            and Path(found).resolve() != venv.resolve()
        ]
        self.binary = others[0] if others else "hawk"

    async def _hawk(self, *args: str) -> str:
        from inspect_ai.util import subprocess as run

        result = await run(
            [self.binary, *args], text=True, env={"HAWK_API_URL": self.api_url}, timeout=60
        )
        if not result.success:
            raise RuntimeError(f"hawk {' '.join(args)} failed: {result.stderr[-1500:]}")
        return str(result.stdout).strip()

    @staticmethod
    def _runner_hook() -> Any:
        """Hawk's token refresh hook, when this process runs inside a Hawk runner."""
        from inspect_ai.hooks._hooks import get_all_hooks

        return next(
            (h for h in get_all_hooks() if type(h).__module__ == "hawk.runner.refresh_token"),
            None,
        )

    async def access_token(self) -> str:
        if self._runner_hook() is not None:
            from inspect_ai.hooks._hooks import override_api_key

            # asked with the runner's own gateway credential: hawk >= 3.7 answers an
            # empty ask only for a provider it routed through its gateway
            token = override_api_key("HAWK_ACCESS_TOKEN", os.environ.get("BASE_API_KEY", ""))
            if not token:
                raise RuntimeError("Hawk runner's token refresh hook gave no token")
            return token
        # fetched each time, not cached: the login refreshes it, a copy expires
        return await self._hawk("auth", "access-token")

    async def refresh_token(self) -> str:
        """The credential a child job's runner renews its access with: this user's."""
        # access first: refreshing it may rotate the hook's refresh token
        if (hook := self._runner_hook()) is not None:
            token = getattr(hook, "_current_refresh_token", None)
        else:
            token = await self._hawk("auth", "refresh-token")
        if not token:
            raise RuntimeError("no Hawk refresh credential for child jobs: run `hawk login`")
        return token


class HawkRuns:
    """Jobs on Hawk, through `hawk.client.HawkClient`."""

    def __init__(
        self, api_url: str, *, credential_mode: Literal["restricted", "user"] = "user"
    ) -> None:
        self.login = HawkLogin(api_url)
        self.credential_mode = credential_mode

    @asynccontextmanager
    async def _client(self) -> AsyncGenerator[Any]:
        from hawk.client import HawkClient

        async with HawkClient(
            token=await self.login.access_token(), api_url=self.login.api_url
        ) as client:
            yield client

    async def submit(self, job: Job) -> str:
        from hawk.client import HawkClient
        from hawk.core.types import EvalSetConfig

        restricted = self.credential_mode == "restricted"
        if restricted and (
            "credential_mode" not in inspect.signature(HawkClient.create_eval_set).parameters
            or not {"transcripts", "additional_models"}.issubset(EvalSetConfig.model_fields)
        ):
            raise RuntimeError(
                "restricted jobs need a Hawk client with restricted-credential and transcript-input support"
            )
        settings = hawk_config(job, restricted=restricted)
        # the job's own code may read Hawk (an audit fetching hawk: logs); Hawk does
        # not tell a runner where its API is
        settings["runner"] = {"environment": {"HAWK_API_URL": self.login.api_url}}
        config = EvalSetConfig.model_validate(settings)
        # the access token first: refreshing it may rotate the refresh token
        token = await self.login.access_token()
        async with HawkClient(token=token, api_url=self.login.api_url) as client:
            if restricted:
                # No fallback: old clients/deployments must refuse this mode.
                options: dict[str, Any] = {"credential_mode": "restricted"}
                created: Any = await client.create_eval_set(config, **options)
            else:
                refresh_token = await self.login.refresh_token()
                created = await client.create_eval_set(config, refresh_token=refresh_token)
        # a newer server's client may return the API body rather than the id
        if isinstance(created, dict):
            return str(created["eval_set_id"])
        return str(getattr(created, "eval_set_id", created))

    async def status(self, run_id: str) -> str:
        async with self._client() as client:
            found = await client.get_job_status(run_id)
        if found is None:
            return FAILED
        status = str(found.status)
        if status == "complete":
            return COMPLETE
        if status in ("failed", "deleted"):
            return FAILED
        if status == "stuck":
            return STUCK
        return RUNNING

    async def logs(self, run_id: str, out_dir: Path) -> list[Path]:
        from hawk.cli.download import download_file

        out_dir.mkdir(parents=True, exist_ok=True)
        files: list[Path] = []
        async with self._client() as client:
            names = [str(f["name"]) for f in await client.get_log_files(run_id)]
            names = [n for n in names if n.endswith(".eval")]
            async for url, filename in client.get_download_urls(names):
                dest = out_dir / Path(filename).name
                await download_file(url, dest)
                files.append(dest)
        return sorted(files)

    async def runner_log(self, run_id: str) -> str:
        async with self._client() as client:
            entries = await client.fetch_logs(run_id, limit=120)
        lines = [
            f"[{e.timestamp:%H:%M:%S}] {e.message}"
            for e in sorted(entries, key=lambda e: e.timestamp)
        ]
        return "\n".join(lines)[-6000:] or "(no runner log yet)"

    async def stop(self, run_id: str) -> None:
        async with self._client() as client:
            await client.stop_eval_set(run_id)


class LocalRuns:
    """Jobs as detached `inspect eval-set` processes, logs under `root/<id>/`.

    Models are OpenRouter ids, run through Inspect's `openrouter` provider on this
    process's OPENROUTER_API_KEY; `provider` exists so tests can run `mockllm`.
    """

    def __init__(self, root: Path, provider: str = "openrouter") -> None:
        self.root = root
        self.provider = provider
        self.root.mkdir(parents=True, exist_ok=True)

    def _dir(self, run_id: str) -> Path:
        path = (self.root / run_id).resolve()
        if path.parent != self.root.resolve() or not path.is_dir():
            raise ValueError(f"no local run {run_id!r}")
        return path

    async def submit(self, job: Job) -> str:
        run_dir = Path(tempfile.mkdtemp(prefix=f"{job.label}-", dir=self.root))
        args = [
            sys.executable,
            "-m",
            "inspect_ai",
            "eval-set",
            job.task,
            "--model",
            ",".join(f"{self.provider}/{m}" for m in job.models),
            "--log-dir",
            str(run_dir / "logs"),
            "--epochs",
            str(job.epochs),
            "--retry-attempts",
            "0",
            "--display",
            "plain",
        ]
        if job.task_args:
            # a file, not -T: inspect parses each -T value as YAML and splits commas,
            # so "a,b" or "yes" would not reach the task as Hawk passes them
            (run_dir / "task_args.json").write_text(json.dumps(job.task_args))
            args += ["--task-config", str(run_dir / "task_args.json")]
        for role, model in job.model_roles.items():
            args += ["--model-role", f"{role}={self.provider}/{model}"]
        if job.sample_ids is not None:
            args += ["--sample-id", ",".join(job.sample_ids)]
        if job.sample_range is not None:
            args += ["--limit", f"{job.sample_range[0]}-{job.sample_range[1]}"]
        await asyncio.to_thread(_start, args, run_dir)
        return run_dir.name

    def _alive(self, run_dir: Path) -> bool:
        try:
            pid = int((run_dir / "pid").read_text())
            os.kill(pid, 0)
        except (OSError, ValueError):
            return False
        # a finished child is a zombie until reaped; it no longer runs
        try:
            done, _ = os.waitpid(pid, os.WNOHANG)
        except ChildProcessError:  # not our child (another process started it)
            return True
        return done == 0

    async def status(self, run_id: str) -> str:
        from inspect_ai.log import list_eval_logs, read_eval_log

        run_dir = self._dir(run_id)
        if (run_dir / "stopped").exists():
            return STOPPED
        if self._alive(run_dir):
            return RUNNING
        logs = list_eval_logs(str(run_dir / "logs"))
        if logs and all(
            read_eval_log(log.name, header_only=True).status == "success" for log in logs
        ):
            return COMPLETE
        return FAILED

    async def logs(self, run_id: str, out_dir: Path) -> list[Path]:
        run_dir = self._dir(run_id)
        return sorted((run_dir / "logs").glob("*.eval"))

    async def runner_log(self, run_id: str) -> str:
        path = self._dir(run_id) / "runner.log"
        lines = path.read_text(errors="replace").splitlines() if path.exists() else []
        return "\n".join(lines[-120:]) or "(no runner log yet)"

    async def stop(self, run_id: str) -> None:
        run_dir = self._dir(run_id)
        (run_dir / "stopped").write_text("")
        if self._alive(run_dir):
            # the whole process group: `inspect eval-set` and anything it started
            os.killpg(int((run_dir / "pid").read_text()), signal.SIGTERM)
            await asyncio.sleep(0)


# what a local job may see of this process's environment: enough to find Python,
# Docker and the model key. Nothing else the operator has set reaches benchmark code.
JOB_ENVIRONMENT = (
    "PATH",
    "HOME",
    "TMPDIR",
    "LANG",
    "LC_ALL",
    "DOCKER_HOST",
    "DOCKER_CONFIG",
    "DOCKER_CONTEXT",
    "OPENROUTER_API_KEY",
)


def _start(args: list[str], run_dir: Path) -> None:
    """Start a detached process group, its output and pid in the run's directory."""
    env = {k: os.environ[k] for k in JOB_ENVIRONMENT if k in os.environ}
    with open(run_dir / "runner.log", "wb") as out:
        process = subprocess.Popen(
            args, stdout=out, stderr=subprocess.STDOUT, start_new_session=True, env=env
        )
    (run_dir / "pid").write_text(str(process.pid))
