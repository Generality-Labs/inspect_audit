"""A job and its lifecycle: one description, two places to run it (see local, hawk).

A job is an Inspect eval set: one task, its arguments, the samples, the models. The
auditor says what to run; a backend decides where. `HawkRuns` submits it
through Hawk's own client, as whoever this process is logged in as, so its model
calls bill to the same key as the auditor's. `LocalRuns` starts
`inspect eval-set` in a detached process on this machine, with this process's
environment and so its key. Both hand back finished logs as local files.
"""

import re
from dataclasses import dataclass, field
from enum import StrEnum
from importlib.metadata import version
from pathlib import Path
from typing import Any, Protocol

from inspect_ai.model import ModelConfig

# The Inspect every job and box runs on: the one installed here, which pyproject pins
# exactly. Hawk swaps in its own build unless a job names one, so every job names this.
INSPECT_VERSION = version("inspect-ai")

# A variant job's replacement files (FRICTION 73): a change to the benchmark, not a
# second benchmark carried in a job config
VARIANT_MAX_FILES = 50
VARIANT_MAX_BYTES = 256 * 1024


class JobState(StrEnum):
    """A job's lifecycle: the one vocabulary every backend reports in.

    Each backend translates its own words in one place (`hawk.hawk_state`,
    `local.LocalRuns.status`).
    """

    RUNNING = "running"
    COMPLETE = "complete"
    FAILED = "failed"
    STOPPED = "stopped"
    # Hawk's own verdict that a job has stopped making progress: worth a person's look
    STUCK = "stuck"


FINISHED = frozenset({JobState.COMPLETE, JobState.FAILED, JobState.STOPPED})


class JobKind(StrEnum):
    """What a job is for, as the auditor launches it and its record keeps it."""

    BENCHMARK = "benchmark"  # the benchmark's own task: fresh attempts
    VARIANT = "variant"  # the benchmark with changed code
    REGRADE = "regrade"  # the benchmark's scorers on recorded attempts
    INVESTIGATION = "investigation"  # an investigator per item

    @property
    def task(self) -> str | None:
        """The task this package runs for the kind; a plain run is the benchmark's own."""
        return None if self is JobKind.BENCHMARK else f"inspect_audit/{self.value}"

    @classmethod
    def of(cls, task: str) -> "JobKind | None":
        """The kind whose task this is; None for the benchmark's own task."""
        return next((k for k in cls if k.task == task), None)


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
    # exact versions this run installs in place of the benchmark's lockfile pins (and
    # the jobs' Inspect): `name==version`; a run's results may turn on them
    package_overrides: list[str] = field(default_factory=list)
    environment: dict[str, str] = field(default_factory=dict)
    model_configs: dict[str, ModelConfig] = field(default_factory=dict)


PINNED = r"[A-Za-z0-9][A-Za-z0-9._-]*==[A-Za-z0-9][A-Za-z0-9.+!_-]*"


def package_name(spec: str) -> str:
    """The canonical distribution name of a `name==version` or `name @ url` spec."""
    import re

    name = re.split(r"[\s=<>!~@\[;]", spec.strip(), maxsplit=1)[0]
    return re.sub(r"[-_.]+", "-", name).lower()


def with_overrides(pins: list[str], overrides: list[str]) -> list[str]:
    """`pins` with every package an override names replaced by that override."""
    replaced = {package_name(o) for o in overrides}
    return [p for p in pins if package_name(p) not in replaced] + list(overrides)


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
    async def status(self, run_id: str) -> JobState: ...
    async def logs(self, run_id: str, out_dir: Path) -> list[Path]: ...
    async def runner_log(self, run_id: str) -> str: ...
    async def stop(self, run_id: str) -> None: ...
