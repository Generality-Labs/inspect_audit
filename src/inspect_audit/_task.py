"""Replace the benchmark solver with an investigator while retaining sample setup.

Task overrides are explicit; unsupported environments raise UnsupportedBenchmarkError.
"""

import inspect as pyinspect
import tempfile
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Literal

from inspect_ai import Task, task_with
from inspect_ai.dataset import Sample
from inspect_ai.model import GenerateConfig, Model, ModelConfig, get_model
from inspect_ai.scorer import Scorer
from inspect_ai.solver import Solver
from inspect_ai.tool import WebSearchProviders
from inspect_ai.util import registry_create, registry_info

from ._compose import UnsupportedBenchmarkError, investigation_compose, their_compose, their_sandbox
from ._investigator import investigator
from ._k8s import investigation_k8s
from ._logs import LogIndex, LogMismatchError, index_logs
from ._source import SourceCopy, collect_directory, collect_source
from ._verdicts import investigation_items, item_scorer

# task settings of theirs the investigation keeps on purpose: `setup` prepares their box,
# `cleanup` tears their resources down, `model` is the eval's own --model (the
# investigator), `version` and `description` describe their task
KEPT = frozenset({"setup", "cleanup", "model", "version", "description"})


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    return list(value) if isinstance(value, list) else [value]


def _task_source(task: Task, name: str, source: str | None) -> SourceCopy:
    """The benchmark code to stage.

    `source=` a `.py` file: imports followed from it within its package. A directory:
    copied whole. Otherwise the task's own file, which Inspect records only for tasks
    not from an installed package.
    """
    if source is not None:
        path = Path(source)
        if path.is_file() and path.suffix == ".py":
            return collect_source(path)
        if path.is_dir():
            return collect_directory(path)
        raise ValueError(f"source={source!r} is neither a .py file nor a directory")
    file = getattr(task, "__task_file__", None)
    if not file:
        raise UnsupportedBenchmarkError(
            f"task {name!r} comes from an installed package, for which Inspect records no "
            "source file: pass source= with the task's .py file"
        )
    return collect_source(Path(file))


def check_same_items(index: LogIndex, samples: list[Sample]) -> None:
    """Each investigated sample's input must be the input its logged attempts were given."""
    from inspect_ai.log import read_eval_log_sample

    for sample in samples:
        one_per_log = {a.log: a for a in index.for_sample(str(sample.id))}
        for attempt in one_per_log.values():
            logged = read_eval_log_sample(
                attempt.log, id=attempt.sample_id, epoch=attempt.epoch, exclude_fields={"events"}
            ).input
            if logged != sample.input:
                raise LogMismatchError(
                    f"sample {sample.id}: the installed benchmark's input differs from the "
                    f"input logged in {attempt.log}"
                )


def judge_roles(index: LogIndex | None, judge: dict[str, str] | None) -> dict[str, str]:
    """The model roles their scorers grade with: recorded in the logs, or declared."""
    if index is None:
        if judge is None:
            raise ValueError(
                "without logs, declare their scorers' judge: judge={role: model}, "
                "or judge={} when their scorers call no model"
            )
        return dict(judge)
    declared = dict(judge or {})
    for role, model in declared.items():
        recorded = index.judges.get(role)
        if recorded is not None and recorded != model:
            raise LogMismatchError(
                f"judge {role}={model!r} differs from the logs' recorded {recorded!r}"
            )
    roles = {**index.judges, **declared}
    # a scorer that judged with the evaluated model (get_model() with no role) would
    # judge with the investigator here
    unbound = index.scorer_models - set(roles.values())
    if unbound & index.eval_models:
        raise UnsupportedBenchmarkError(
            f"the recorded scorer judged with the evaluated model ({', '.join(sorted(unbound))}) "
            "rather than a model role; in an investigation that model would be the investigator"
        )
    return roles


def numbered(task: Task) -> tuple[list[Sample], bool]:
    """A task's samples with the ids an eval gives them, and whether those are positions.

    Samples without ids get Inspect's own rule, position + 1 over the whole dataset
    before any selection, exactly as an eval would number them.
    """
    dataset = list(task.dataset)
    missing = [s for s in dataset if s.id is None]
    positional = bool(missing)
    if positional and len(missing) != len(dataset):
        raise UnsupportedBenchmarkError("some of the benchmark's samples have ids and some do not")
    if positional:
        dataset = [s.model_copy(update={"id": i + 1}) for i, s in enumerate(dataset)]
    return dataset, positional


def investigation_task(
    task: str | Task,
    *,
    samples: Sequence[str] | Literal["all"],
    checks: Sequence[str],
    task_args: dict[str, Any] | None = None,
    logs: Sequence[str] | None = None,
    judge: dict[str, str] | None = None,
    judge_configs: dict[str, dict[str, Any]] | None = None,
    source: str | None = None,
    compose: str | None = None,
    assessment_ids: dict[str, list[str]] | None = None,
    redact: Sequence[str] = (),
    confidential: bool = False,
    notes: str | None = None,
    search: WebSearchProviders | None = None,
    investigator_image: str | None = None,
    cost_limit: float | None = None,
    message_limit: int | None = None,
    time_limit: int | None = None,
    working_limit: int | None = None,
    investigator_model: Model | None = None,
) -> Task:
    """Build the investigation of a benchmark task.

    Args:
        task: The benchmark task: a registry name such as `inspect_evals/simpleqa`,
            or a `Task` (then `task_args` must be empty).
        samples: The sample ids to investigate, or `"all"`.
        checks: The checks to run on each sample, by skill name.
        task_args: The benchmark task's arguments, exactly as the logs ran it.
        logs: `.eval` files recording attempts at this task; every header must
            match the declared task, arguments and installed benchmark version.
        judge: Model roles for their scorers (the LLM judge), role -> model. Taken
            from the logs when given; declared values must agree with them. Required
            without logs: `{}` when their scorers call no model.
        judge_configs: Inspect ModelConfig records for judge roles, including
            generation settings and endpoints. Must agree with recorded roles.
        source: The benchmark's code for the investigator's box: the task's `.py` file (its
            imports within its package are followed, with their folders' data files;
            see `_source`) or a directory copied whole. Required for tasks from an
            installed package; otherwise defaults to the task's own file.
        investigator_model: Optional explicit agent model for programmatic drivers. The
            CLI normally uses the evaluation model. Binding here also works when
            an execution backend overrides Task.model after construction.
        compose: The benchmark's compose file, when its task names only `"docker"`.
        assessment_ids: Assessment-unit ids per sample for question labels.
        redact: Metadata keys withheld from the staged item.
        confidential: The benchmark is unpublished: the investigator must not transmit it.
        notes: A free-form steer for the investigator.
        search: Inspect web_search provider configuration, e.g. {"tavily": True}.
            External providers work across investigator models and need their API key
            in the Inspect runner environment. Omitted means no search tool.
        investigator_image: A published investigator image (required on k8s, which cannot build).
        cost_limit: Dollars per investigated sample, the investigator's and its `grade` judge calls
            together. The investigation is governed by a dollar cap: this one, or the eval's own
            (`--cost-limit`, an eval set's `cost_limit`); a sample with neither refuses to
            start. Inspect refuses a cost-capped run with any unpriced model.
        message_limit: The investigator's message limit (the benchmark's is not inherited).
        time_limit: The investigator's time limit, seconds.
        working_limit: The investigator's working-time limit, seconds.
    """
    if isinstance(task, Task):
        if task_args:
            raise ValueError("task_args apply to a registry name, not a Task object")
        their, name, package = task, task.name, None
    else:
        their = registry_create("task", task, **(task_args or {}))
        name = registry_info(their).name
        package = name.split("/")[0] if "/" in name else None
    scoped = investigation_items(list(checks))

    dataset, _ = numbered(their)
    by_id: dict[str, Sample] = {str(s.id): s for s in dataset}
    if samples == "all":
        chosen = list(by_id)
    else:
        chosen = [str(s) for s in samples]
        unknown = [s for s in chosen if s not in by_id]
        if unknown:
            raise ValueError(f"unknown sample id(s): {', '.join(unknown)}")
    selected = [by_id[s] for s in chosen]
    if assessment_ids is not None and set(assessment_ids) - set(chosen):
        raise ValueError("assessment_ids names samples that are not being investigated")

    index = (
        index_logs(list(logs), task_name=name, task_args=dict(task_args or {}), package=package)
        if logs is not None
        else None
    )
    roles = judge_roles(index, judge)
    configured_roles: dict[str, str | Model] = dict(roles)
    configurations = {
        **(index.judge_configs if index is not None else {}),
        **{
            role: ModelConfig.model_validate(value) for role, value in (judge_configs or {}).items()
        },
    }
    for role, configured in configurations.items():
        if roles.get(role) != configured.model:
            raise ValueError(f"judge configuration for {role!r} must match its declared model")
        if (
            index is not None
            and role in index.judge_configs
            and configured != index.judge_configs[role]
        ):
            raise LogMismatchError(
                f"judge configuration for {role!r} differs from the recorded configuration"
            )
        model_kwargs: dict[str, Any] = dict(configured.args)
        model_kwargs["memoize"] = False
        configured_roles[role] = get_model(
            configured.model,
            config=configured.config,
            base_url=configured.base_url,
            **model_kwargs,
        )
    if index is not None:
        # every log that attempted an investigated sample must have given it this sample's
        # input: positional ids under a shuffled run, a changed dataset or a different
        # variant all fail here, item by item, rather than by a rule about the run
        check_same_items(index, selected)

    stage = Path(tempfile.mkdtemp(prefix="inspect_audit_"))
    spec = their_sandbox(their.sandbox, selected)
    if spec is None and compose is not None:
        raise UnsupportedBenchmarkError("compose= was given but the benchmark declares no sandbox")
    if spec is not None and spec.type == "k8s":
        if compose is not None:
            raise UnsupportedBenchmarkError(
                "compose= applies to Docker; declare the k8s config on the task"
            )
        sandbox = investigation_k8s(
            spec, stage=stage / "sandbox", investigator_image=investigator_image
        )
    else:
        sandbox = investigation_compose(
            their_compose(spec, compose, stage) if spec is not None else None,
            stage=stage / "sandbox",
            investigator_image=investigator_image,
        )

    their_scorers: list[Scorer] = _as_list(their.scorer)
    their_setup: list[Solver] = _as_list(their.setup)
    solver = investigator(
        checks=scoped,
        their_scorers=their_scorers,
        their_setup=their_setup,
        their_samples=by_id,
        benchmark_box=spec is not None,
        index=index,
        source=_task_source(their, name, source),
        assessment_ids=assessment_ids,
        redact=redact,
        confidential=confidential,
        notes=notes,
        search=search,
        model=investigator_model,
    )

    # every setting of theirs that would govern the investigator is replaced, not inherited
    overrides: dict[str, Any] = {
        # Their sample declarations were resolved into the merged task sandbox.
        # Leaving one here lets Inspect override that merge and lose our service.
        "dataset": [s.model_copy(update={"sandbox": None}) for s in selected],
        "solver": solver,
        "scorer": [item_scorer(check) for check in scoped],
        "metrics": None,
        "sandbox": sandbox,
        "config": GenerateConfig(),
        "model_roles": configured_roles or None,
        "epochs": 1,
        "approval": None,
        "checkpoint": None,
        "early_stopping": None,
        "fail_on_error": None,
        "continue_on_fail": None,
        "score_on_error": None,
        "review": None,
        "on_checkpoint": None,
        "on_resume": None,
        "headline_metric": None,
        "sentinel": None,
        "tags": None,
        "viewer": None,
        "message_limit": message_limit,
        "token_limit": None,
        "turn_limit": None,
        "time_limit": time_limit,
        "working_limit": working_limit,
        "cost_limit": cost_limit,
        "name": f"investigation/{name}",
        "metadata": {
            "investigated_task": name,
            "investigated_task_args": dict(task_args or {}),
            "investigated_logs": list(logs or []),
            "judge_roles": roles,
        },
    }
    accepted = set(pyinspect.signature(task_with).parameters)
    unclassified = accepted - set(overrides) - KEPT - {"task"}
    if unclassified:
        # a new Inspect added a task setting: decide whether the investigation overrides or keeps
        # it before it can quietly govern the investigator
        raise RuntimeError(
            f"task_with takes {', '.join(sorted(unclassified))}, which the investigation neither "
            "overrides nor keeps; classify them in _task.py"
        )
    return task_with(their, **{k: v for k, v in overrides.items() if k in accepted})
