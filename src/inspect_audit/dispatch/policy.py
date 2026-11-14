"""Immutable audit permissions and the single child-job authorizer."""

import hashlib
import json
import re
from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Literal

from inspect_ai.model import ModelConfig
from inspect_ai.solver import Generate, Solver, TaskState, solver
from inspect_ai.tool import ToolError
from inspect_ai.util import StoreModel
from pydantic import BaseModel, ConfigDict, Field, model_validator

from .jobs import (
    INSPECT_VERSION,
    PINNED,
    VARIANT_MAX_BYTES,
    VARIANT_MAX_FILES,
    Job,
    JobKind,
    package_name,
    validate_environment,
    with_overrides,
)


def canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


class FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Models(FrozenModel):
    auditor: tuple[str, ...] = ()
    investigators: tuple[str, ...] = ()
    subjects: tuple[str, ...] = ()
    judges: tuple[str, ...] = ()


class Limits(FrozenModel):
    max_concurrent_jobs: int | None = Field(default=None, gt=0, strict=True)
    # one budget for the auditor's own calls and every job it launches: launches stop
    # once measured spend reaches it; nothing is reserved for running jobs, whose
    # spend the capped key bounds (FRICTION 22, 76)
    budget_usd: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    # "full": findings cannot be submitted while any permitted sample has no take on
    # any policy check; the auditor finishes the work or reports why it cannot
    # (report_blocker). "any": submit at whatever coverage (FRICTION 25)
    coverage: Literal["any", "full"] = "any"


class Permissions(FrozenModel):
    models: Models = Models()
    limits: Limits = Limits()


# TEMPORARY (FRICTION 93): our Hawk has no secure token service yet, so a restricted
# submission is refused ("Restricted credentials aren't enabled on this deployment") and
# no child job can start. Until it is enabled, an audit may state `credential_mode: user`
# for Hawk children: they then run with the operator's own login. James allowed it on
# 2026-11-13 for the smoke audits (public evals); delete this, and the configs that state
# it, once restricted launches work.
USER_CHILDREN_ON_HAWK = (
    "Child jobs on Hawk run with the operator's own login (credential_mode: user), "
    "because this Hawk has no secure token service yet (FRICTION 93)"
)


def credential_mode(runs: str | None, stated: str | None) -> Literal["restricted", "user"]:
    """How child jobs authenticate: the one place this is decided.

    Restricted on Hawk unless the audit states otherwise (FRICTION 73, 93); local jobs
    run as the operator, since restricted credentials exist only on Hawk.
    """
    mode = stated or ("restricted" if runs == "hawk" else "user")
    if mode not in ("restricted", "user"):
        raise ValueError("credential_mode must be 'restricted' or 'user'")
    if mode == "restricted" and runs != "hawk":
        raise ValueError("restricted credentials require runs='hawk'")
    return "restricted" if mode == "restricted" else "user"


class LaunchPolicy(FrozenModel):
    """All nested values are immutable; arbitrary configuration is canonical JSON."""

    task: str | None
    task_package: str | None
    task_pins: tuple[str, ...] = ()
    investigation_package: str | None
    investigator_image: str
    models: Models
    samples: tuple[str, ...] | None = None
    checks: tuple[str, ...]
    task_args_json: str = "{}"
    environment_json: str = "{}"
    source: str | None = None
    supplied_logs: tuple[str, ...] = ()
    limits: Limits = Limits()
    credential_mode: Literal["restricted", "user"] = "user"
    model_configs_json: str = "{}"
    configuration_json: str

    def benchmark_packages(self, overrides: list[str] | tuple[str, ...] = ()) -> list[str]:
        """The benchmark as its jobs install it; the one place this list is built.

        Its package, then its pins with any version overrides applied.
        """
        if not self.task_package:
            raise ToolError("the launch policy names no benchmark package")
        return [self.task_package, *with_overrides(list(self.task_pins), list(overrides))]

    def box_install(self) -> list[str]:
        """The benchmark for a box (the auditor's or an investigator's).

        As its jobs install it, on the Inspect every job runs on.
        """
        return [*self.benchmark_packages(), f"inspect-ai=={INSPECT_VERSION}"]

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
    auditor_model: str | None = None


def ensure_policy(policy: LaunchPolicy, saved: PolicyStore, *, require_existing: bool) -> None:
    snapshot = canonical(policy.model_dump(mode="json"))
    if saved.policy_json is None and saved.digest is None and not require_existing:
        saved.policy_json, saved.digest = snapshot, policy.digest
    elif saved.policy_json != snapshot or saved.digest != policy.digest:
        raise RuntimeError(
            "Audit configuration differs from its saved launch policy. "
            "Resume with the original configuration or start a new audit."
        )


def ensure_model(policy: LaunchPolicy, saved: PolicyStore, model: str) -> None:
    if saved.auditor_model is None:
        saved.auditor_model = model
    elif saved.auditor_model != model:
        raise RuntimeError("the auditor model cannot change during an audit")
    if policy.models.auditor and model not in policy.models.auditor:
        raise RuntimeError("the auditor model is outside the launch policy")


@solver
def bind_policy(policy: LaunchPolicy) -> Solver:
    """Bind once to Inspect's checkpointed store before agent execution."""

    async def solve(state: TaskState, generate: Generate) -> TaskState:
        ensure_policy(policy, state.store_as(PolicyStore), require_existing=False)
        ensure_model(policy, state.store_as(PolicyStore), str(state.model))
        return state

    return solve


# Benchmark arguments are the operator's declaration (a job must match them exactly),
# so they choose sandboxes, images and paths freely; the one choice refused is moving
# the benchmark's code out of a sandbox and into the runner, beside its credentials.
SANDBOX_ARG = re.compile(r"(^|_)sandbox(_type)?$", re.IGNORECASE)
MODEL_ARG = re.compile(r"(^|_)(model|judge|grader)s?($|_)", re.IGNORECASE)


def argument_problems(
    args: Any, allowed: list[str], where: str = "task_args", model_value: bool = False
) -> list[str]:
    if isinstance(args, str) and model_value and "/" in args and args not in allowed:
        return [f"{where}: {args!r} is not an allowed judge model"]
    problems: list[str] = []
    if isinstance(args, dict):
        for key, value in args.items():
            here = f"{where}.{key}"
            if SANDBOX_ARG.search(str(key)) and str(value).strip().lower() == "local":
                problems.append(f"{here}: the benchmark may not run outside a sandbox")
            else:
                problems += argument_problems(
                    value, allowed, here, model_value or bool(MODEL_ARG.search(str(key)))
                )
    elif isinstance(args, list):
        for index, value in enumerate(args):
            problems += argument_problems(value, allowed, f"{where}[{index}]", model_value)
    return problems


# the arguments an auditor-launched investigation may carry; every one must be a
# parameter of the registered `investigation` task (a test holds them together)
INVESTIGATION_ARGS = frozenset(
    {
        "task",
        "task_args",
        "samples",
        "checks",
        "source",
        "logs",
        "judge",
        "investigator_image",
        "notes",
        "paper",
        "install",
    }
)

# the same for a regrade job, against the registered `regrade` task
REGRADE_ARGS = frozenset(
    {
        "task",
        "task_args",
        "logs",
        "judge",
        "samples",
        "epochs",
        "files",
        "source",
    }
)

# a variant job: the benchmark with replacement files laid over its package (FRICTION 73)
VARIANT_ARGS = frozenset({"task", "task_args", "files", "source"})


def files_problem(files: Any) -> str | None:
    """Why `files` is not a small set of replacement files, or None.

    The same limits as `_variant`'s, so a job built by hand is refused here too.
    """
    if not isinstance(files, dict) or not files:
        return "a variant carries its replacement files"
    if not all(isinstance(k, str) and isinstance(v, str) for k, v in files.items()):
        return "variant files map paths to text"
    if (
        len(files) > VARIANT_MAX_FILES
        or sum(len(v.encode()) for v in files.values()) > VARIANT_MAX_BYTES
    ):
        return "variant files exceed the size limit"
    return None


def with_caching(name: str, configured: ModelConfig | None) -> ModelConfig | None:
    """Anthropic models through OpenRouter: cache the conversation, not only the prompt.

    Inspect's OpenRouter provider marks cache breakpoints only on list-content messages,
    and an agent's messages are strings, so each turn re-bought the whole history
    (FRICTION 17; UKGovernmentBEIS/inspect_ai#5873, fix PR #6283 unmerged on
    2026-11-12). The workaround: Inspect's markers off, OpenRouter's automatic caching
    on. An operator's own `cache_prompt` or `extra_body` is left as declared.
    """
    if not name.startswith("openrouter/anthropic/"):
        return configured
    current = configured or ModelConfig(model=name)
    config = current.config
    if config.cache_prompt is not None or config.extra_body is not None:
        return current
    return current.model_copy(
        update={
            "config": config.model_copy(
                update={
                    "cache_prompt": False,
                    "extra_body": {"cache_control": {"type": "ephemeral"}},
                }
            )
        }
    )


def cached(configs: dict[str, ModelConfig], names: Any) -> dict[str, ModelConfig]:
    """`configs` with `with_caching` applied to every model in `names`."""
    out = dict(configs)
    for name in names:
        if (config := with_caching(str(name), out.get(str(name)))) is not None:
            out[str(name)] = config
    return out


def check_overrides(overrides: list[str], kind: JobKind) -> None:
    """The one check on a job's version overrides (`packages`).

    Each is an exact pin, one per package, of a package already installed beside the
    benchmark: an override changes a version, it does not bring in new code (a reviewer
    had a job approved to install an arbitrary PyPI name, FRICTION 117). This package's
    own version is fixed, and so is Inspect's for rescores and variants, which run in it.
    """
    from importlib.metadata import PackageNotFoundError, distribution

    fixed = {"inspect-audit"} | (
        {"inspect-ai"} if kind in (JobKind.REGRADE, JobKind.VARIANT) else set()
    )
    for spec in overrides:
        if not re.fullmatch(PINNED, spec):
            raise ToolError(f"{spec!r}: a version override is an exact pin, name==version")
        name = package_name(spec)
        if name in fixed:
            raise ToolError(f"{spec!r}: this run cannot change that package's version")
        try:
            distribution(name)
        except PackageNotFoundError:
            raise ToolError(
                f"{spec!r}: {name} is not installed beside the benchmark; an override "
                "changes the version of a package it already uses"
            ) from None
    if len({package_name(o) for o in overrides}) != len(overrides):
        raise ToolError("one version override per package")


@dataclass(frozen=True)
class AuditAuthorizer:
    """Approve a complete job before the backend can submit it."""

    policy: LaunchPolicy

    @staticmethod
    def allow(models: list[str], allowed: tuple[str, ...], part: str) -> None:
        refused = [m for m in models if m not in allowed]
        if refused:
            raise ToolError(
                f"{', '.join(refused)} may not be {part}; allowed: {', '.join(allowed) or 'none'}"
            )

    def _regrade(self, job: Job, known_logs: set[str]) -> Job:
        """A regrade: the benchmark's own scorers on recorded attempts, judges only."""
        p = self.policy
        args = job.task_args
        if job.task != JobKind.REGRADE.task or job.package != p.investigation_package:
            raise ToolError("regrade task and package must match the launch policy")
        overrides = list(job.package_overrides)
        check_overrides(overrides, JobKind.REGRADE)
        if job.extra_packages != p.benchmark_packages(overrides):
            raise ToolError("job dependencies must match the launch policy")
        if set(args) - REGRADE_ARGS or job.model_roles or job.sample_range is not None:
            raise ToolError("regrade job contains configuration outside the launch contract")
        if "files" in args and (problem := files_problem(args["files"])):
            raise ToolError(problem)
        if "files" in args and (p.source is None or args.get("source") != p.source):
            raise ToolError("a variant needs the policy's declared benchmark.source module")
        if args.get("task") != p.task or canonical(args.get("task_args", {})) != p.task_args_json:
            raise ToolError("regrade task and task_args are fixed by the launch policy")
        judges = [str(m) for m in (args.get("judge") or {}).values()]
        # runs as its judge, or, for a grader that calls no model, as a mock model
        if job.models != (judges[:1] or ["mockllm/model"]):
            raise ToolError("a regrade runs as its judge (a mock model when it has none)")
        self.allow(judges, p.models.judges, "judges")
        if not args.get("logs") or set(args["logs"]) - (set(p.supplied_logs) | known_logs):
            raise ToolError("regrade inputs are outside the launch policy")
        selected = args.get("samples")
        if not isinstance(selected, list) or not selected:
            raise ToolError("a regrade must select sample IDs")
        if p.samples is not None and set(selected) - set(p.samples):
            raise ToolError("sample selection is outside the launch policy")
        epochs = args.get("epochs")
        if isinstance(epochs, bool) or not isinstance(epochs, int) or epochs < 1:
            raise ToolError("regrade epochs must be a positive whole number")
        approved = deepcopy(job)
        configs = {
            name: ModelConfig.model_validate(value)
            for name, value in json.loads(p.model_configs_json).items()
        }
        approved.model_configs = cached(
            {name: configs[name] for name in job.models if name in configs}, job.models
        )
        approved.environment = json.loads(p.environment_json)
        return approved

    def authorise(self, job: Job, kind: JobKind, known_logs: set[str]) -> Job:
        p = self.policy
        if job.environment and canonical(job.environment) != p.environment_json:
            raise ToolError("job environment must match the launch policy")
        # a run folder's and a Hawk eval set's name: no paths, nothing Hawk would refuse
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,47}", job.label):
            raise ToolError("a label is letters, digits, - and _, at most 48 characters")
        try:
            kind = JobKind(kind)
        except ValueError:
            raise ToolError(f"unknown job kind: {kind}") from None
        if kind is JobKind.REGRADE:
            return self._regrade(job, known_logs)
        ours = kind in (JobKind.INVESTIGATION, JobKind.VARIANT)
        expected_task = kind.task or p.task
        expected_package = p.investigation_package if ours else p.task_package
        if job.task != expected_task or job.package != expected_package:
            raise ToolError("job task and package must match the launch policy")
        overrides = list(job.package_overrides)
        if overrides and kind is JobKind.INVESTIGATION:
            raise ToolError("investigations run the benchmark as declared: no version overrides")
        check_overrides(overrides, kind)
        benchmark = p.benchmark_packages(overrides) if p.task_package else []
        if job.extra_packages != (benchmark if ours else benchmark[1:]):
            raise ToolError("job dependencies must match the launch policy")
        allowed = p.models.investigators if kind is JobKind.INVESTIGATION else p.models.subjects
        if not job.models:
            raise ToolError("a job must declare its models")
        self.allow(
            job.models,
            allowed,
            "the investigator" if kind is JobKind.INVESTIGATION else "evaluated",
        )
        if isinstance(job.epochs, bool) or not isinstance(job.epochs, int) or job.epochs < 1:
            raise ToolError("epochs must be positive")
        args = job.task_args
        if kind is JobKind.INVESTIGATION:
            if set(args) - INVESTIGATION_ARGS or job.model_roles or job.sample_range is not None:
                raise ToolError(
                    "investigation job contains configuration outside the launch contract"
                )
            if not isinstance(args.get("notes") or "", str):
                raise ToolError("investigation notes must be text")
            if not isinstance(args.get("paper") or "", str):
                raise ToolError("investigation paper must be a path or URL")
            if args.get("task") != p.task or args.get("investigator_image") != p.investigator_image:
                raise ToolError("investigation task and image must match the launch policy")
            source = args.get("source")
            if not isinstance(source, str) or not re.fullmatch(
                r"[A-Za-z_]\w*(\.[A-Za-z_]\w*)*", source
            ):
                raise ToolError("investigation source must be a Python module name")
            if not args.get("checks") or set(args["checks"]) - set(p.checks):
                raise ToolError("investigation checks are outside the launch policy")
            selected = args.get("samples")
            if not isinstance(selected, list) or not selected:
                raise ToolError("an investigation must select sample IDs")
            if set(args.get("logs") or []) - (set(p.supplied_logs) | known_logs):
                raise ToolError("investigation inputs are outside the launch policy")
            if p.source is not None and args.get("source") != p.source:
                raise ToolError("investigation source must match the launch policy")
            roles = args.get("judge") or {}
            benchmark_args = args.get("task_args", {})
        elif kind is JobKind.VARIANT:
            if set(args) - VARIANT_ARGS or args.get("task") != p.task:
                raise ToolError("variant job contains configuration outside the launch contract")
            if p.source is None or args.get("source") != p.source:
                raise ToolError("a variant needs the policy's declared benchmark.source module")
            if problem := files_problem(args.get("files")):
                raise ToolError(problem)
            selected = job.sample_ids
            roles = job.model_roles
            benchmark_args = args.get("task_args", {})
            if job.sample_ids is not None and job.sample_range is not None:
                raise ToolError("select sample IDs or a sample range, not both")
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
            [str(model) for model in roles.values()],
            p.models.judges,
            "judges",
        )
        if problems := argument_problems(benchmark_args, list(p.models.judges)):
            raise ToolError("; ".join(problems))
        # No caller-owned mutable lists/dicts cross the authorization boundary.
        approved = deepcopy(job)
        configs = {
            name: ModelConfig.model_validate(value)
            for name, value in json.loads(p.model_configs_json).items()
        }
        selected_models = set(job.models) | set(roles.values())
        expected_configs = {name: configs[name] for name in selected_models if name in configs}
        if job.model_configs and job.model_configs != expected_configs:
            raise ToolError("model configuration must match the launch policy")
        approved.model_configs = cached(expected_configs, selected_models)
        if kind is JobKind.INVESTIGATION and expected_configs:
            approved.task_args["judge_configs"] = {
                role: configs[name].model_dump(mode="json", exclude_none=True)
                for role, name in roles.items()
                if name in configs
            }
        approved.environment = json.loads(p.environment_json)
        return approved
