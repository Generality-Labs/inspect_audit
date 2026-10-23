"""Jobs on this machine: Inspect's own detached eval sets."""

import asyncio
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from .jobs import COMPLETE, FAILED, FINISHED, RUNNING, STOPPED, Job, validate_environment


class LocalRuns:
    """Jobs as Inspect's own detached eval sets: `inspect eval-set --detach`.

    Inspect starts the run in its own session, prints a launch record (process id,
    log folder, output file) once the run is live, and ends the output file with a
    `done` record carrying overall success. The record is kept in the run's folder;
    status is that `done` record, or the process being gone without one. Logs live
    under `root/<id>/logs`.

    Model identities and configurations are passed to Inspect unchanged.
    """

    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    def _dir(self, run_id: str) -> Path:
        path = (self.root / run_id).resolve()
        if path.parent != self.root.resolve() or not path.is_dir():
            raise ValueError(f"no local run {run_id!r}")
        return path

    def _launch(self, run_id: str) -> dict[str, Any]:
        return dict(json.loads((self._dir(run_id) / "launch.json").read_text()))

    def address(self, run_id: str) -> str:
        return str(self._dir(run_id) / "logs")

    async def submit(self, job: Job) -> str:
        run_dir = Path(tempfile.mkdtemp(prefix=f"{job.label}-", dir=self.root))
        args = [
            sys.executable,
            "-m",
            "inspect_ai",
            "eval-set",
            job.task,
            "--log-dir",
            str(run_dir / "logs"),
            "--epochs",
            str(job.epochs),
            "--retry-attempts",
            "0",
            "--detach",
        ]
        for model in job.models:
            configured = job.model_configs.get(model)
            if configured is None:
                args += ["--model-spec", json.dumps({"model": model})]
            else:
                args += [
                    "--model-spec",
                    json.dumps(
                        {
                            "model": model,
                            **configured.config.model_dump(exclude_none=True),
                            "base_url": configured.base_url,
                            "model_args": configured.args,
                        }
                    ),
                ]
        if job.task_args:
            # a file, not -T: inspect parses each -T value as YAML and splits commas,
            # so "a,b" or "yes" would not reach the task as Hawk passes them
            (run_dir / "task_args.json").write_text(json.dumps(job.task_args))
            args += ["--task-config", str(run_dir / "task_args.json")]
        for role, model in job.model_roles.items():
            configured = job.model_configs.get(model)
            value = (
                model
                if configured is None
                else json.dumps(
                    {
                        "model": model,
                        **configured.config.model_dump(exclude_none=True),
                        "model_args": {
                            **configured.args,
                            **({"base_url": configured.base_url} if configured.base_url else {}),
                        },
                    }
                )
            )
            args += ["--model-role", f"{role}={value}"]
        if job.sample_ids is not None:
            args += ["--sample-id", ",".join(job.sample_ids)]
        if job.sample_range is not None:
            args += ["--limit", f"{job.sample_range[0]}-{job.sample_range[1]}"]
        credentials: dict[str, str] = {}
        from inspect_ai.model import get_model

        for name in set(job.models) | set(job.model_roles.values()):
            configured = job.model_configs.get(name)
            model_kwargs: dict[str, Any] = dict(configured.args) if configured else {}
            model_kwargs["memoize"] = False
            model = get_model(
                name,
                config=configured.config if configured else None,
                base_url=configured.base_url if configured else None,
                **model_kwargs,
            )
            for variable in model.api.api_key_vars:
                if variable in os.environ:
                    credentials[variable] = os.environ[variable]
        launched = await asyncio.to_thread(_detach, args, {**credentials, **job.environment})
        (run_dir / "launch.json").write_text(json.dumps(launched))
        return run_dir.name

    def _done(self, run_id: str) -> dict[str, Any] | None:
        """Inspect's `done` record from the run's output file, once it has finished."""
        output = Path(self._launch(run_id).get("output_file") or "")
        if not output.is_file():
            return None
        # Inspect ends its output with a small JSON done record.
        with output.open("rb") as handle:
            size = handle.seek(0, os.SEEK_END)
            handle.seek(max(0, size - 64 * 1024))
            tail = handle.read().decode(errors="replace")
        for line in reversed(tail.splitlines()):
            try:
                record = json.loads(line)
            except ValueError:
                continue
            if isinstance(record, dict) and record.get("event") == "done":
                return record
        return None

    async def status(self, run_id: str) -> str:
        done = self._done(run_id)
        if done is not None:
            if (self._dir(run_id) / "stopped").exists():
                return STOPPED
            return COMPLETE if done.get("success") else FAILED
        try:
            os.kill(int(self._launch(run_id)["pid"]), 0)
        except (OSError, KeyError, TypeError, ValueError):
            return FAILED  # gone without a done record: it died mid-flight
        return RUNNING

    async def logs(self, run_id: str, out_dir: Path) -> list[Path]:
        return sorted((self._dir(run_id) / "logs").glob("*.eval"))

    async def runner_log(self, run_id: str) -> str:
        output = Path(self._launch(run_id).get("output_file") or "")
        lines = output.read_text(errors="replace").splitlines() if output.is_file() else []
        return "\n".join(lines[-120:]) or "(no runner log yet)"

    async def stop(self, run_id: str) -> None:
        from inspect_ai.util import subprocess as run

        if await self.status(run_id) in FINISHED:
            return
        launch = self._launch(run_id)
        command = [sys.executable, "-m", "inspect_ai", "ctl", "task"]
        listed = await run([*command, "list", "--json"], timeout=60)
        if not listed.success:
            raise RuntimeError(f"could not list Inspect tasks: {listed.stderr[-1500:]}")
        tasks = [
            task
            for task in json.loads(listed.stdout)["tasks"]
            if task["pid"] == launch["pid"]
            and Path(task["log_location"].removeprefix("file://")).resolve().parent
            == (self._dir(run_id) / "logs").resolve()
            and task.get("completed_at") is None
        ]
        if not tasks:
            if await self.status(run_id) in FINISHED:
                return
            raise RuntimeError(f"no live Inspect tasks found for local run {run_id!r}")
        for task in tasks:
            cancelled = await run([*command, "cancel", task["task_id"], "--json"], timeout=60)
            if not cancelled.success:
                raise RuntimeError(f"could not cancel Inspect task: {cancelled.stderr[-1500:]}")
        # A request is not completion: status waits for Inspect's done record.
        (self._dir(run_id) / "stopped").write_text("")


JOB_ENVIRONMENT = (
    "PATH",
    "HOME",
    "TMPDIR",
    "LANG",
    "LC_ALL",
    "DOCKER_HOST",
    "DOCKER_CONFIG",
    "DOCKER_CONTEXT",
)


def _detach(args: list[str], environment: dict[str, str] | None = None) -> dict[str, Any]:
    """Run `inspect ... --detach` and return its launch record.

    The launcher returns once the run is live (or fails pre-flight, non-zero), so
    this is the whole handoff. The child sees JOB_ENVIRONMENT plus declared settings.
    """
    env = {k: os.environ[k] for k in JOB_ENVIRONMENT if k in os.environ}
    env.update(validate_environment(environment or {}))
    result = subprocess.run(args, capture_output=True, text=True, env=env, timeout=600, check=False)
    if result.returncode != 0:
        raise RuntimeError(f"inspect eval-set did not start: {result.stderr[-1500:]}")
    records = [
        json.loads(line) for line in result.stdout.splitlines() if line.strip().startswith("{")
    ]
    launch = next((r for r in records if r.get("event") in ("launch", "done")), None)
    if launch is None:
        raise RuntimeError(f"inspect eval-set printed no launch record: {result.stdout[-500:]}")
    return launch
