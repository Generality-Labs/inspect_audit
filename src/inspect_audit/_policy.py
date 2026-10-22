"""Immutable investigation permissions and the single child-job authorizer."""

import hashlib
import json
import math
import re
from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Literal

from inspect_ai.solver import Generate, Solver, TaskState, solver
from inspect_ai.tool import ToolError
from inspect_ai.util import StoreModel
from pydantic import BaseModel, ConfigDict, Field, model_validator

from ._runs import Job, validate_environment


def canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


class FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Models(FrozenModel):
    investigator: tuple[str, ...] = ()
    auditors: tuple[str, ...] = ()
    subjects: tuple[str, ...] = ()
    judges: tuple[str, ...] = ()


class Limits(FrozenModel):
    audit_sample_cost_usd: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    max_concurrent_jobs: int | None = Field(default=None, gt=0, strict=True)


class Inputs(FrozenModel):
    logs: tuple[str, ...] = ()


class Permissions(FrozenModel):
    models: Models = Models()
    inputs: Inputs = Inputs()
    limits: Limits = Limits()


class Benchmark(FrozenModel):
    task: str = Field(min_length=1)
    package: str | None = None
    repo: str | None = None
    revision: str | None = None
    source: str | None = None
    # Serialised below into the frozen launch policy; never retained as a mutable dict.
    task_args: dict[str, Any] = Field(default_factory=dict)
    environment: dict[str, str] = Field(default_factory=dict)


class AuditScope(FrozenModel):
    samples: tuple[str, ...] | Literal["all"] = "all"
    checks: tuple[str, ...] = Field(min_length=1)


class Execution(FrozenModel):
    backend: Literal["hawk", "local"]
    credential_mode: Literal["restricted", "user"]

    @model_validator(mode="before")
    @classmethod
    def default_credentials(cls, data: Any) -> Any:
        # restricted on Hawk, the operator's own on a laptop: declared, not inferred later
        if isinstance(data, dict) and "credential_mode" not in data:
            backend = data.get("backend")  # pyright: ignore[reportUnknownMemberType,reportUnknownVariableType]
            return {**data, "credential_mode": "restricted" if backend == "hawk" else "user"}
        return data  # pyright: ignore[reportUnknownVariableType]

    @model_validator(mode="after")
    def local_credentials(self) -> "Execution":
        if self.backend == "local" and self.credential_mode == "restricted":
            raise ValueError(
                "restricted credentials require Hawk; local execution uses user credentials"
            )
        return self


class LaunchPolicy(FrozenModel):
    """All nested values are immutable; arbitrary configuration is canonical JSON."""

    task: str | None
    task_package: str | None
    audit_package: str | None
    auditor_image: str
    models: Models
    samples: tuple[str, ...] | None = None
    checks: tuple[str, ...]
    task_args_json: str = "{}"
    environment_json: str = "{}"
    source: str | None = None
    supplied_logs: tuple[str, ...] = ()
    limits: Limits = Limits()
    credential_mode: Literal["restricted", "user"] = "user"
    configuration_json: str

    @model_validator(mode="after")
    def unique_scope(self) -> "LaunchPolicy":
        validate_environment(json.loads(self.environment_json))
        if self.samples is not None and (
            not self.samples or len(set(self.samples)) != len(self.samples)
        ):
            raise ValueError("sample scope must be nonempty and contain unique IDs")
        return self

    @property
    def digest(self) -> str:
        return hashlib.sha256(canonical(self.model_dump(mode="json")).encode()).hexdigest()


class PolicyStore(StoreModel):
    """Checkpointed policy snapshot, compared with the host's frozen authority."""

    policy_json: str | None = None
    digest: str | None = None
    investigator_model: str | None = None


def ensure_policy(policy: LaunchPolicy, saved: PolicyStore, *, require_existing: bool) -> None:
    snapshot = canonical(policy.model_dump(mode="json"))
    if saved.policy_json is None and saved.digest is None and not require_existing:
        saved.policy_json, saved.digest = snapshot, policy.digest
    elif saved.policy_json != snapshot or saved.digest != policy.digest:
        raise RuntimeError(
            "Investigation configuration differs from its saved launch policy. "
            "Resume with the original configuration or start a new investigation."
        )


def ensure_model(policy: LaunchPolicy, saved: PolicyStore, model: str) -> None:
    if saved.investigator_model is None:
        saved.investigator_model = model
    elif saved.investigator_model != model:
        raise RuntimeError("the investigator model cannot change during an investigation")
    if (
        policy.models.investigator
        and model.removeprefix("openrouter/") not in policy.models.investigator
    ):
        raise RuntimeError("the investigator model is outside the launch policy")


@solver
def bind_policy(policy: LaunchPolicy) -> Solver:
    """Bind once to Inspect's checkpointed store before agent execution."""

    async def solve(state: TaskState, generate: Generate) -> TaskState:
        ensure_policy(policy, state.store_as(PolicyStore), require_existing=False)
        ensure_model(policy, state.store_as(PolicyStore), str(state.model))
        return state

    return solve


# A job cannot move execution out of its declared environment through task arguments.
PLACEMENT_ARG = re.compile(
    r"(^|_)(sandbox|image|docker|compose|path|filepath|filename|file|dir|directory|workdir|root|url|host)s?($|_)",
    re.IGNORECASE,
)
MODEL_ARG = re.compile(r"(^|_)(model|judge|grader)s?($|_)", re.IGNORECASE)


def argument_problems(args: Any, allowed: list[str], where: str = "task_args") -> list[str]:
    problems: list[str] = []
    if isinstance(args, dict):
        for key, value in args.items():
            here = f"{where}.{key}"
            if PLACEMENT_ARG.search(str(key)):
                problems.append(f"{here}: jobs may not set where code runs or what it reads")
            elif MODEL_ARG.search(str(key)) and isinstance(value, str):
                if value.removeprefix("openrouter/") not in allowed or "/" not in value:
                    problems.append(f"{here}: {value!r} is not an allowed judge model")
            else:
                problems += argument_problems(value, allowed, here)
    elif isinstance(args, list):
        for index, value in enumerate(args):
            problems += argument_problems(value, allowed, f"{where}[{index}]")
    return problems


@dataclass(frozen=True)
class InvestigationAuthorizer:
    """Approve a complete job before the backend can submit it."""

    policy: LaunchPolicy

    @staticmethod
    def allow(models: list[str], allowed: tuple[str, ...], part: str) -> None:
        refused = [m for m in models if m not in allowed]
        if refused:
            raise ToolError(
                f"{', '.join(refused)} may not be {part}; allowed: {', '.join(allowed) or 'none'}"
            )

    def authorise(self, job: Job, kind: str, known_logs: set[str]) -> Job:
        p = self.policy
        if job.environment and canonical(job.environment) != p.environment_json:
            raise ToolError("job environment must match the launch policy")
        # a run folder's and a Hawk eval set's name: no paths, nothing Hawk would refuse
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,47}", job.label):
            raise ToolError("a label is letters, digits, - and _, at most 48 characters")
        if kind not in ("audit", "benchmark"):
            raise ToolError(f"unknown job kind: {kind}")
        expected_task = "inspect_audit/audit" if kind == "audit" else p.task
        expected_package = p.audit_package if kind == "audit" else p.task_package
        if job.task != expected_task or job.package != expected_package:
            raise ToolError("job task and package must match the launch policy")
        if job.extra_packages != ([p.task_package] if kind == "audit" else []):
            raise ToolError("job dependencies must match the launch policy")
        allowed = p.models.auditors if kind == "audit" else p.models.subjects
        if not job.models:
            raise ToolError("a job must declare its models")
        self.allow(job.models, allowed, "the auditor" if kind == "audit" else "evaluated")
        if isinstance(job.epochs, bool) or not isinstance(job.epochs, int) or job.epochs < 1:
            raise ToolError("epochs must be positive")
        args = job.task_args
        if kind == "audit":
            permitted = {
                "task",
                "task_args",
                "samples",
                "checks",
                "source",
                "logs",
                "judge",
                "auditor_image",
                "cost_limit",
                "model_prices",
            }
            if set(args) - permitted or job.model_roles or job.sample_range is not None:
                raise ToolError("audit job contains configuration outside the launch contract")
            if args.get("task") != p.task or args.get("auditor_image") != p.auditor_image:
                raise ToolError("audit task and image must match the launch policy")
            source = args.get("source")
            if not isinstance(source, str) or not re.fullmatch(
                r"[A-Za-z_]\w*(\.[A-Za-z_]\w*)*", source
            ):
                raise ToolError("audit source must be a Python module name")
            if not args.get("checks") or set(args["checks"]) - set(p.checks):
                raise ToolError("audit checks are outside the launch policy")
            selected = args.get("samples")
            if not isinstance(selected, list) or not selected:
                raise ToolError("an audit must select sample IDs")
            if set(args.get("logs") or []) - (set(p.supplied_logs) | known_logs):
                raise ToolError("audit inputs are outside the launch policy")
            if p.source is not None and args.get("source") != p.source:
                raise ToolError("audit source must match the launch policy")
            roles = args.get("judge") or {}
            cost = args.get("cost_limit")
            if (
                isinstance(cost, bool)
                or not isinstance(cost, (int, float))
                or not math.isfinite(cost)
                or cost <= 0
            ):
                raise ToolError("audit cost_limit must be a finite positive number")
            cap = p.limits.audit_sample_cost_usd
            if cap is not None and cost > cap:
                raise ToolError(f"audit cost_limit exceeds the policy's ${cap:g} per sample")
            benchmark_args = args.get("task_args", {})
        else:
            selected = job.sample_ids
            roles = job.model_roles
            benchmark_args = args
            if job.sample_ids is not None and job.sample_range is not None:
                raise ToolError("select sample IDs or a sample range, not both")
        if p.samples is not None and (
            selected is None or job.sample_range is not None or set(selected) - set(p.samples)
        ):
            raise ToolError("sample selection is outside the launch policy")
        if canonical(benchmark_args) != p.task_args_json:
            raise ToolError("benchmark task_args are fixed by the launch policy")
        self.allow(
            [str(model).removeprefix("openrouter/") for model in roles.values()],
            p.models.judges,
            "judges",
        )
        # The operator approved these exact arguments in the frozen policy above.
        # Argument names are not permissions: e.g. HLE's judge_prompt="original"
        # selects a prompt, not a model. Models supplied through roles remain checked.
        # No caller-owned mutable lists/dicts cross the authorization boundary.
        approved = deepcopy(job)
        approved.environment = json.loads(p.environment_json)
        return approved
