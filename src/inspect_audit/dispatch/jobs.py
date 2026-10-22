"""A job and its lifecycle: one description, two places to run it (see local, hawk).

A job is an Inspect eval set: one task, its arguments, the samples, the models. The
investigator says what to run; a backend decides where. `HawkRuns` submits it
through Hawk's own client, as whoever this process is logged in as, so its model
calls bill to the same key as the investigator's. `LocalRuns` starts
`inspect eval-set` in a detached process on this machine, with this process's
environment and so its key. Both hand back finished logs as local files.
"""

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from inspect_ai.model import ModelConfig

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
    models: list[str]  # Full Inspect names, e.g. openrouter/openai/gpt-6-luna.
    task_args: dict[str, Any] = field(default_factory=dict)
    model_roles: dict[str, str] = field(default_factory=dict)
    sample_ids: list[str] | None = None
    # positions in the dataset, first to last counting from 1, inclusive: how a dataset
    # whose samples carry no ids is selected (Inspect filters ids before numbering them)
    sample_range: tuple[int, int] | None = None
    epochs: int = 1
    extra_packages: list[str] = field(default_factory=list)
    environment: dict[str, str] = field(default_factory=dict)
    model_configs: dict[str, ModelConfig] = field(default_factory=dict)


def validate_environment(environment: dict[str, str]) -> dict[str, str]:
    """Copy explicit runner settings; credentials use the separate secrets channel."""
    if not isinstance(environment, dict) or any(
        not isinstance(name, str)
        or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name)
        or not isinstance(value, str)
        or "\x00" in value
        for name, value in environment.items()
    ):
        raise ValueError("benchmark_environment must map environment variable names to strings")
    if "HAWK_API_URL" in environment:
        raise ValueError("declare Hawk's API with hawk_api_url, not benchmark_environment")
    return dict(environment)


class Runs(Protocol):
    """A place jobs run. Ids are the backend's own."""

    async def submit(self, job: Job) -> str: ...
    def address(self, run_id: str) -> str: ...
    async def status(self, run_id: str) -> str: ...
    async def logs(self, run_id: str, out_dir: Path) -> list[Path]: ...
    async def runner_log(self, run_id: str) -> str: ...
    async def stop(self, run_id: str) -> None: ...
