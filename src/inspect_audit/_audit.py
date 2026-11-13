"""Top-level audit agent, staged inputs and checkpointed child-job records."""

import asyncio
import inspect as inspect_module
import json
import os
import re
import shutil
from dataclasses import dataclass, field
from logging import getLogger
from pathlib import Path
from typing import Any, Literal, cast

from inspect_ai import Task, task
from inspect_ai.agent import AgentState, as_solver, react
from inspect_ai.dataset import Sample
from inspect_ai.log import (
    EvalSampleSummary,
    read_eval_log_sample_summaries_async,
)
from inspect_ai.model import CompactionSummary, GenerateConfig, ModelConfig
from inspect_ai.solver import Generate, Solver, TaskState, solver
from inspect_ai.tool import Tool, ToolDef, ToolError, bash, skill, tool
from inspect_ai.util import CheckpointConfig, sandbox, store_as

from . import prompts
from ._audit_workspace import docker_box
from ._image import view_image
from ._prices import cost_of, price_models
from ._register import register_view
from ._report import (
    AuditRecord,
    AuditState,
    audit_outcome,
    end_if_blocked,
    operator_turn,
    report_blocker,
)
from ._repository import only_task, paper_from_metadata, paths_from_metadata
from ._spend import Spend, budget
from ._staging import prepare_workspace
from ._sync import run
from ._verdicts import LOG_SKILLS, SKILLS, SUPPORT_SKILLS
from .containers import AUDITOR_IMAGE, INVESTIGATOR_IMAGE
from .dispatch import (
    FINISHED,
    HawkRuns,
    Job,
    JobKind,
    LocalRuns,
    Runs,
    validate_environment,
)
from .dispatch.jobs import add_usage, settled
from .dispatch.packages import git_package_spec, own_package_spec
from .dispatch.policy import (
    USER_CHILDREN_ON_HAWK,
    AuditAuthorizer,
    LaunchPolicy,
    Permissions,
    PolicyStore,
    bind_policy,
    canonical,
    ensure_model,
    ensure_policy,
)
from .dispatch.policy import (
    credential_mode as resolve_credential_mode,
)

logger = getLogger(__name__)

ASSETS = Path(__file__).parent / "audit"

AUDIT_SKILLS = (
    "auditing",
    "running-jobs",
    "eval-validity-review",
    "investigate-dataset",
    "security-audit-eval",
    "check-trajectories-workflow",
)
# compact the auditor's context once it passes this many tokens
COMPACTION_TOKENS = 200_000


class Platform:
    """Where jobs run, as the agent's tools see it.

    A backend call that fails reaches the agent as a tool error naming the call, to
    work around or report, not as a crash.

    Only the backend's calls are wrapped: our own code failing still fails loudly
    (AGENTS rule 7). A wait, a submission or a status check on a flaky Hawk is the
    agent's to retry; it no longer ends the audit (FRICTION 115).
    """

    def __init__(self, runs: Runs) -> None:
        self.runs = runs

    def address(self, run_id: str) -> str:
        return self.runs.address(run_id)

    def __getattr__(self, name: str) -> Any:
        method = getattr(self.runs, name)
        if not inspect_module.iscoroutinefunction(method):
            return method

        async def call(*args: Any, **kwargs: Any) -> Any:
            try:
                return await method(*args, **kwargs)
            except ToolError:
                raise
            except Exception as ex:
                shown = ", ".join(str(a) for a in args if not isinstance(a, Path))
                raise ToolError(f"{name}({shown}) failed: {type(ex).__name__}: {ex}") from ex

        return call


def require_models(models: list[str], runs: Runs | None, api_url: str | None = None) -> None:
    """Every model the audit may use, checked once, before anything starts.

    Hawk must price each one (`price_models`, which registers the prices `_spend` and the
    auditor's own cost limit use: FRICTION 84, 90) and serve it to this user under that
    name (Hawk checks a job's models only when it is submitted, after the audit has
    spent: FRICTION 53). A failure names every model at fault.
    """
    price_models(models, api_url)
    unservable = getattr(runs, "unservable", None)
    if unservable is not None and models and (missing := run(unservable(models))):
        raise ValueError(
            "The policy names models Hawk's proxy does not serve under those names: "
            f"{', '.join(missing)}. Jobs naming them would be refused. Add them "
            "(hawk proxy models add) or take them out of the policy."
        )


@dataclass
class Audit:
    """What the job tools need that the agent does not hold."""

    root: Path
    runs: Runs
    task: str  # the investigated task's registry name
    task_package: str
    investigation_package: str
    investigator_image: str
    supplied: list[str]  # log sources the operator gave, as addresses a job can read
    authorizer: AuditAuthorizer
    spend: Spend
    # exact pins installed beside the benchmark in every child job (its lockfile's)
    task_pins: list[str] = field(default_factory=list)
    # the operator's paper (path or URL), passed on so investigators read it too
    paper: str | None = None
    launch_lock: asyncio.Lock = field(default_factory=asyncio.Lock, init=False, repr=False)

    def __post_init__(self) -> None:
        # every backend call the tools make reaches the agent as a tool error if it fails
        if not isinstance(self.runs, Platform):
            self.runs = cast(Runs, Platform(self.runs))

    def record(self) -> AuditRecord:
        return store_as(AuditRecord)

    async def stop_unsettled(self, record: AuditRecord) -> list[str]:
        """Stop every job that could still run or spend: the audit that owned it is over.

        Task cleanup, so it runs when the auditor submits, is blocked, hits a limit or
        errors. A killed pod skips it, and there the jobs keep running for the resumed
        audit to collect. A job that will not stop is logged, not raised: cleanup must
        reach every job. Returns the labels stopped.
        """
        stopped: list[str] = []
        for job in record.jobs:
            if job.get("stop_requested"):
                continue
            try:
                if settled(await self.runs.status(job["run_id"]), False):
                    continue
                await self.runs.stop(job["run_id"])
            except Exception as ex:
                logger.warning("could not stop %s at the audit's end: %s", job["label"], ex)
                continue
            stopped.append(job["label"])
        record.jobs = [
            {**j, "stop_requested": True, "stopped_by": "audit end"} if j["label"] in stopped else j
            for j in record.jobs
        ]
        if stopped:
            logger.warning("stopped jobs still running at the audit's end: %s", ", ".join(stopped))
        return stopped

    def job(self, label: str) -> dict[str, Any]:
        found = [j for j in self.record().jobs if j["label"] == label]
        if not found:
            raise ToolError(f"no job {label!r}; jobs(action='list') shows them")
        return found[0]

    async def launch(self, job: Job, kind: JobKind, note: str, diff: str | None = None) -> str:
        async with self.launch_lock:
            from ._findings import require_audit

            require_audit()
            return await self._launch(job, kind, note, diff)

    async def _launch(self, job: Job, kind: JobKind, note: str, diff: str | None = None) -> str:
        policy = self.authorizer.policy
        approved = self.authorizer.authorise(
            job, kind, {str(j["address"]) for j in self.record().jobs}
        )
        # This object is made at launch; sandbox policy files are never read here.
        ensure_policy(policy, store_as(PolicyStore), require_existing=True)
        if any(j["label"] == job.label for j in self.record().jobs):
            raise ToolError(f"a job labelled {job.label!r} exists; choose another label")
        cap = policy.limits.max_concurrent_jobs
        if cap is not None:
            active = sum(
                [
                    await self.runs.status(str(j["run_id"])) not in FINISHED
                    for j in self.record().jobs
                ]
            )
            if active >= cap:
                raise ToolError(f"the policy permits at most {cap} concurrent jobs")
        await self.spend.refuse_if_spent()
        run_id = await self.runs.submit(approved)
        address = self.runs.address(run_id)
        # assigned back, not appended in place: the store records an assignment
        record = self.record()
        record.jobs = [
            *record.jobs,
            {
                "label": job.label,
                "kind": kind.value,
                "run_id": run_id,
                "address": address,
                "note": note,
                "policy_sha256": policy.digest,
                # a variant's change, kept with the job so findings can cite it exactly
                **({"diff": diff} if diff else {}),
                **({"packages": job.package_overrides} if job.package_overrides else {}),
            },
        ]
        return (
            f"Launched {kind} job {job.label!r} ({address}). jobs(action='wait', "
            f"label='{job.label}') blocks until it finishes; jobs(action='collect', ...) "
            f"then brings its logs to /inputs/jobs/{job.label}/."
        )


def json_object(text: str, name: str) -> dict[str, Any]:
    """A JSON object argument. Strict tool schemas cannot carry a free-form object."""
    try:
        value = json.loads(text or "{}")
    except json.JSONDecodeError as ex:
        raise ToolError(f"{name} must be a JSON object: {ex}") from ex
    if not isinstance(value, dict):
        raise ToolError(f"{name} must be a JSON object, not {type(value).__name__}")
    return value


def _logs_named(inv: Audit, names: list[str]) -> list[str]:
    """Job labels and supplied sources as addresses a job can read."""
    addresses = []
    for name in names:
        known = [j for j in inv.record().jobs if j["label"] == name]
        if known:
            addresses.append(known[0]["address"])
        elif name in inv.supplied:
            addresses.append(name)
        else:
            raise ToolError(
                f"{name!r} is neither a job of yours nor a supplied source "
                f"({', '.join(inv.supplied) or 'none supplied'})"
            )
    return addresses


async def _variant_files(inv: Audit, folder: str) -> tuple[dict[str, str], str]:
    """The replacement files in a folder of the box, checked; and their diff."""
    from ._variant import check_overlay

    source = inv.authorizer.policy.source
    if not source:
        raise ToolError(
            "modified code needs the module that defines the task: the operator declares benchmark.source"
        )
    root = folder.rstrip("/")
    # Python's bytecode caches, written when the auditor imports its change to test it,
    # are not part of the change (smoke round 7: a .pyc crashed the read, FRICTION 97)
    listed = await sandbox().exec(
        ["find", root, "-name", "__pycache__", "-prune", "-o", "-type", "f", "-print"]
    )
    if not listed.success:
        raise ToolError(f"cannot list {root}: {listed.stderr.strip()}")
    files: dict[str, str] = {}
    for path in sorted(listed.stdout.split("\n")):
        if not path.strip():
            continue
        try:
            files[path[len(root) + 1 :]] = await sandbox().read_file(path)
        except UnicodeDecodeError:
            raise ToolError(
                f"{path} is not text: a code change replaces source files only; remove it"
            ) from None
    try:
        return files, check_overlay(inv.task, source, files)
    except ValueError as ex:
        raise ToolError(str(ex)) from ex


def _selection(samples: list[str] | None) -> tuple[list[str] | None, tuple[int, int] | None]:
    """Sample ids, or for a benchmark whose items have no ids, a contiguous run of positions."""
    from ._register import population, started

    current = population()
    if samples is None or not (started(current) and current.subject.get("ids") == "positional"):
        return samples, None
    try:
        numbers = sorted(int(s) for s in samples)
    except ValueError as ex:
        raise ToolError(
            "this benchmark's items have no ids: select them by position, 1, 2, ..."
        ) from ex
    if numbers != list(range(numbers[0], numbers[-1] + 1)):
        raise ToolError(
            "this benchmark's items have no ids: select one contiguous run of positions"
        )
    return None, (numbers[0], numbers[-1])


@tool
def run_eval(inv: Audit) -> Tool:
    async def execute(
        label: str,
        samples: list[str] | None,
        models: list[str],
        judge: str,
        epochs: int,
        rescore: list[str],
        code: str | None,
        packages: list[str],
        note: str,
    ) -> str:
        """Run the benchmark, as it is or changed: new evidence about how it behaves.

        Fresh attempts by `models`, or with `rescore`, the benchmark's grader run again
        on attempts already recorded. With `code`, the benchmark is modified first; with
        `packages`, it runs on other versions of its dependencies: ablations. Its task
        arguments are the launch policy's. Collect the job's logs with `jobs`.

        Args:
            label: A short name for this job, unique in the audit.
            samples: The items to run, by the benchmark's sample ids (positions 1, 2, ...
                when its items have none); null for every permitted item.
            models: Models to make fresh attempts, from the subject list; [] when rescoring.
            judge: Role name to model for the benchmark's judge, as a JSON object from the
                judge list; "{}" when it has none. When rescoring it must be the model
                the logs record.
            epochs: Attempts per item and model; when rescoring, how many times each
                recorded attempt is scored again (disagreeing epochs are judge noise).
                Each epoch is a full run: it multiplies the cost.
            rescore: Recorded attempts to score again instead of making new ones: labels
                of your jobs, or supplied log sources; [] for fresh attempts.
            code: A folder in your box holding the files you changed, each at its path
                inside the installed package (e.g. inspect_evals/simpleqa/simpleqa.py);
                every other file is the original. Null for the unmodified benchmark.
            packages: Exact versions to install instead of the benchmark's own, as
                name==version (e.g. ["numpy==1.26.4"]; "inspect-ai==..." changes Inspect,
                except with `code`); [] for the benchmark's. Hawk runs only.
            note: Why you are running this.
        """
        policy = inv.authorizer.policy
        judges = {str(k): str(v) for k, v in json_object(judge, "judge").items()}
        files, diff = await _variant_files(inv, code) if code else (None, None)
        args = json.loads(policy.task_args_json)
        if rescore:
            if models:
                raise ToolError("rescoring makes no new attempts: models must be []")
            # only the benchmark's own attempts can be graded again: a variant's are
            # graded in its own run, and the other kinds' logs hold no attempts of the
            # benchmark (round 8: refused only after launch, FRICTION 101)
            others = [
                f"{j['label']} ({j['kind']})"
                for j in inv.record().jobs
                if j["label"] in rescore and j["kind"] != JobKind.BENCHMARK
            ]
            if others:
                raise ToolError(
                    f"rescore takes the benchmark's own runs or the supplied logs, not "
                    f"{', '.join(others)}: a variant's attempts are graded in its own run"
                )
            if samples is None:
                raise ToolError("rescoring selects the items to score again by sample id")
            job = Job(
                label=label,
                task=str(JobKind.REGRADE.task),
                package=inv.investigation_package,
                extra_packages=policy.benchmark_packages(packages),
                package_overrides=list(packages),
                # the judge as the eval's model: never called, but it is how the job
                # gets the judge's credentials; a grader that calls no model (judge {},
                # e.g. SciCode's tests) still needs a model to run under
                models=list(judges.values())[:1] or ["mockllm/model"],
                task_args={
                    "task": inv.task,
                    "task_args": args,
                    "logs": _logs_named(inv, rescore),
                    "judge": judges,
                    "samples": samples,
                    "epochs": epochs,
                    **({"files": files, "source": policy.source} if files else {}),
                },
            )
            return await inv.launch(job, JobKind.REGRADE, note, diff)
        if not models:
            raise ToolError("name the models to make fresh attempts, or give logs to rescore")
        sample_ids, sample_range = _selection(samples)
        variant = files is not None
        job = Job(
            label=label,
            task=str(JobKind.VARIANT.task) if variant else inv.task,
            package=inv.investigation_package if variant else inv.task_package,
            # a variant job is ours, with the benchmark as an extra; a plain run is the
            # benchmark's own package with its pins
            extra_packages=policy.benchmark_packages(packages)[0 if variant else 1 :],
            package_overrides=list(packages),
            task_args=(
                {"task": inv.task, "source": policy.source, "task_args": args, "files": files}
                if variant
                else args
            ),
            models=models,
            model_roles=judges,
            sample_ids=sample_ids,
            sample_range=sample_range,
            epochs=epochs,
        )
        return await inv.launch(job, JobKind.VARIANT if variant else JobKind.BENCHMARK, note, diff)

    return execute


@tool
def send_investigators(inv: Audit) -> Tool:
    async def execute(
        label: str,
        samples: list[str],
        checks: list[str],
        logs: list[str],
        model: str | None,
        judge: str,
        note: str,
    ) -> str:
        """Send investigators: one agent per item, inside the benchmark's own setup.

        Each investigates one item against the checks, can try answers with the
        benchmark's grader, and records a verdict per check. Collect the job with `jobs`;
        its verdicts go into the register.

        Args:
            label: A short name for this job, unique in the audit.
            samples: The benchmark's sample ids to investigate.
            checks: Checks to run, from the launch policy, e.g. ["gold-answer"].
            logs: Recorded attempts the investigators read: labels of your jobs, or
                supplied log sources; [] for none.
            model: The investigator, from the investigator list; null for its first.
            judge: Role name to model for the benchmark's judge, as a JSON object from
                the judge list. With logs, the model the logs record; "{}" for none.
            note: Why you are sending them and what to look for: each investigator reads it.
        """
        policy = inv.authorizer.policy
        judges = {str(k): str(v) for k, v in json_object(judge, "judge").items()}
        investigator = model or (policy.models.investigators or [None])[0]
        if not investigator:
            raise ToolError("the launch policy names no investigator model")
        if not policy.source:
            raise ToolError(
                "investigators read the module that defines the task: the operator declares benchmark.source"
            )
        job = Job(
            label=label,
            task=str(JobKind.INVESTIGATION.task),
            package=inv.investigation_package,
            extra_packages=policy.benchmark_packages(),
            models=[investigator],
            task_args={
                "task": inv.task,
                "task_args": json.loads(policy.task_args_json),
                "samples": samples,
                "checks": checks,
                "source": policy.source,
                "logs": _logs_named(inv, logs) or None,
                # {} is "their grader calls no model", not "unknown": pass it as given
                "judge": judges,
                "investigator_image": inv.investigator_image,
                "notes": note or None,
                "paper": inv.paper,
                # the benchmark into the investigator's own box, as this job installs it
                "install": policy.box_install(),
            },
        )
        return await inv.launch(job, JobKind.INVESTIGATION, note)

    return execute


@tool
def jobs(inv: Audit) -> Tool:
    async def execute(action: str, label: str | None, wait_minutes: float | None) -> str:
        """See and manage the jobs this audit launched.

        Args:
            action: "list" every job; "status" of one; "wait" until one finishes
                or Hawk reports it stuck (spends no tokens); "collect" a finished job's logs into
                /inputs/jobs/<label>/; "log" the tail of its runner's own log, where
                install failures and crashes show; "stop" it.
            label: Which job, for every action but "list".
            wait_minutes: How long "wait" may block; null means twenty.
        """
        from ._findings import require_audit

        require_audit()
        if action == "list":
            rows = inv.record().jobs
            return (
                "\n".join(
                    f"{j['label']} ({j['kind']}) {j['address']}"
                    + (" collected" if j.get("collected") else "")
                    for j in rows
                )
                or "no jobs yet"
            )
        if not label:
            raise ToolError(f"action {action!r} needs a label")
        job = inv.job(label)
        return await _job_action(inv, job, action, wait_minutes)

    return execute


async def _job_action(
    inv: Audit, job: dict[str, Any], action: str, wait_minutes: float | None
) -> str:
    label, run_id = job["label"], job["run_id"]
    if action == "status":
        return f"{label}: {await inv.runs.status(run_id)}"
    if action == "wait":
        deadline = asyncio.get_running_loop().time() + 60 * (wait_minutes or 20)
        while not settled(status := await inv.runs.status(run_id), bool(job.get("stop_requested"))):
            if asyncio.get_running_loop().time() > deadline:
                break
            await asyncio.sleep(30)
        return f"{label}: {status}"
    if action == "log":
        return await inv.runs.runner_log(run_id)
    if action == "stop":
        await inv.runs.stop(run_id)
        record = inv.record()
        record.jobs = [
            {**j, "stop_requested": True} if j["label"] == label else j for j in record.jobs
        ]
        return (
            f"stop requested for {label}; wait for it to finish before collecting. "
            "Completed samples remain in its logs."
        )
    if action == "collect":
        from ._box import copy_into_box

        status = await inv.runs.status(run_id)
        if status not in FINISHED:
            if not settled(status, bool(job.get("stop_requested"))):
                raise ToolError(f"{label} is {status}; wait for it before collecting")
            # stuck, or stopped and gone: nothing will come; recorded, so findings can go on
            record = inv.record()
            record.jobs = [
                {**j, "collection_error": f"{status}: nothing to collect"}
                if j["label"] == label
                else j
                for j in record.jobs
            ]
            return f"{label} is {status} and will produce nothing more; recorded as not collected"
        host = inv.root / "inputs" / "jobs" / label
        files = await inv.runs.logs(run_id, host)
        if not files:
            record = inv.record()
            record.jobs = [
                {**j, "collection_error": f"{status}: no logs"} if j["label"] == label else j
                for j in record.jobs
            ]
            raise ToolError(
                f"{label} ({status}) wrote no logs; the failure was recorded. jobs(action='log') shows why"
            )
        if files[0].parent != host:  # a local run's logs are already on this machine
            host.mkdir(parents=True, exist_ok=True)
            files = [Path(shutil.copy2(f, host / f.name)) for f in files]
        await copy_into_box(files, f"/inputs/jobs/{label}")
        lines = [await _log_line(f) for f in files]
        if job["kind"] == JobKind.INVESTIGATION:
            from ._register import collect_investigation

            lines.append(
                await collect_investigation(files, job["address"], inv.task, inv.authorizer.policy)
            )
        # each file's identity kept with the job (the store is checkpointed; the host's
        # copy is not), so the frozen findings can cite it after a resume (FRICTION 113)
        collected = [
            {
                "source": f"/inputs/jobs/{label}/{f.name}",
                "address": f"{job['address'].rstrip('/')}/{f.name}",
                "sha256": _digest(f),
                "bytes": f.stat().st_size,
            }
            for f in files
        ]
        record = inv.record()
        record.jobs = [
            {**j, "collected": True, "logs": collected} if j["label"] == label else j
            for j in record.jobs
        ]
        return f"{label}: {status}; collected to /inputs/jobs/{label}/\n" + "\n".join(lines)
    raise ToolError("action must be list, status, wait, collect, log or stop")


def _digest(path: Path) -> str:
    import hashlib

    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def _cost(summaries: list[EvalSampleSummary]) -> float:
    """What these samples' tokens cost at the audit's prices.

    The one pricing (`_prices.cost_of`), whatever prices the child itself held.
    """
    usage: dict[str, Any] = {}
    for summary in summaries:
        add_usage(usage, summary.model_usage or {})
    return cost_of(usage)[0]


# Inspect's async readers, never the sync ones: those start their own event loop, which
# inside the auditor's async tools is the race that blocked CL-bench v4 (FRICTION 44, 79)
async def _log_line(path: Path) -> str:
    """One log, summarised from its samples: how many, how they ended, what they cost."""
    summaries = await read_eval_log_sample_summaries_async(str(path))
    errors = sum(1 for s in summaries if s.error)
    return f"  {path.name}: {len(summaries)} samples, {errors} errored, ${_cost(summaries):.2f} recorded"


async def _continue(state: AgentState, interactive: bool) -> bool | str:
    if store_as(AuditState).outcome == "blocked":
        return False
    if not store_as(AuditState).published:
        return (
            True
            if state.output.message.tool_calls
            else "Continue the current phase: audit and submit findings, or use the publishing skill after submission."
        )
    if not interactive:
        return False
    return await operator_turn(state)


@task
def audit(
    repo: str | None = None,
    logs: list[str] | None = None,
    paper: str | None = None,
    docs: list[str] | None = None,
    overview: str | None = None,
    target_task: str | None = None,
    revision: str | None = None,
    paths: list[str] | None = None,
    output_dir: str | None = None,
    runs: str | None = None,
    box: str | None = None,
    hawk_api_url: str | None = None,
    token_limit: str | int | None = None,
    interactive: bool | None = None,
    extra_skills: list[str] | None = None,
    investigation_package: str | None = None,
    task_package: str | None = None,
    task_package_pins: list[str] | None = None,
    investigator_image: str | None = None,
    auditor_image: str | None = None,
    box_resources: dict[str, Any] | None = None,
    artifact_dir: str | None = None,
    instructions: str | None = None,
    policy: dict[str, Any] | None = None,
    task_args: dict[str, Any] | None = None,
    investigation_samples: list[str] | str | None = None,
    checks: list[str] | None = None,
    source: str | None = None,
    credential_mode: str | None = None,
    child_secrets: list[str] | None = None,
    benchmark_environment: dict[str, str] | None = None,
    model_configs: dict[str, dict[str, Any]] | None = None,
    publisher: str | None = None,
) -> Task:
    """Audit a benchmark, submit findings, then use the selected publisher.

    Runs wherever Inspect runs it. The agent's box (`box`) and where its jobs run
    (`runs`) are declared, never inferred. Every model call bills to the key it is
    made on, which is the budget: a capped OpenRouter key locally, the Hawk user's
    assigned key on Hawk.

    Args:
        repo: Local Git repository or HTTPS Git URL of the benchmark.
        logs: Inspect log files or directories, or hawk:<eval set id> sources.
        paper: Local file or URL; a URL is downloaded now (arXiv abs -> pdf). Defaults to
            the paper the eval names in its own metadata.
        docs: Documentation directories to copy into /inputs (inspect docs, Hawk docs).
        overview: Benchmark-specific context, supplied-evidence context and operator
            questions.
        target_task: The task under investigation, e.g. `benchmark/task`. Defaults to the only
            task the repository declares, when there is one.
        revision: Commit to snapshot (default HEAD).
        paths: Repository paths to include in the snapshot. Defaults to the investigated
            task's own directory and the modules its package shares.
        output_dir: Where the host keeps the audit's files, including the
            published report. Defaults to ~/.inspect-audit/audits.
        runs: Where the agent's jobs run: "hawk" or "local". None gives it no jobs.
        box: The agent's own box: "docker" (default), or "k8s" for a pod on Hawk.
        hawk_api_url: Hawk's API, for jobs and balances on Hawk. Defaults to
            HAWK_API_URL, Hawk's own setting.
        token_limit: Optional Inspect token limit (e.g. "output:500k").
        interactive: Wait for the operator over ACP after publishing.
        extra_skills: Additional skill directories to load.
        investigation_package: pip spec of inspect_audit for investigation jobs. Defaults to the
            commit this process is running.
        task_package: pip spec of the benchmark for jobs. Defaults to the source
            repository and revision.
        task_package_pins: exact pins installed beside the benchmark in every child
            job, e.g. its extra's packages from its own uv.lock at that commit
            (`python -m inspect_audit.dispatch.packages REPO COMMIT EXTRA`).
        investigator_image: Published investigator image for investigation jobs on Kubernetes.

        auditor_image: Published auditor sandbox image for Hawk.
        box_resources: Kubernetes `resources` (requests/limits) for the auditor's box on
            k8s; None uses the deployment's sandbox default.
        artifact_dir: Where the published report is also saved, under this sample:
            on Hawk, the job's S3 artifacts directory. None keeps it on the host only.
        instructions: Operator instructions and scope, separate from benchmark background.
        policy: The frozen permissions: `models` (auditor, investigators, subjects,
            judges: full Inspect names, the only place models are named) and `limits`
            (budget_usd, max_concurrent_jobs, coverage). Changes on resume are refused;
            start a new audit instead.
        task_args: Fixed benchmark arguments for every child job and the register.
        investigation_samples: Permitted sample IDs, or "all". Subset requests are allowed.
        checks: Permitted investigation checks; defaults to the package's available checks.
        source: Optional fixed benchmark source module for investigation jobs.
        credential_mode: "restricted" or "user" for Hawk children. Structured
            execution configuration defaults to restricted; legacy calls use user.
        child_secrets: Environment variable names to pass through Hawk's secrets API
            to child runners, e.g. ["HF_TOKEN"] for gated datasets. Names are frozen
            in the launch configuration; values stay out of policy and workspace files.
        benchmark_environment: Non-secret runner settings required by the benchmark,
            applied to every child job and to task loading in this process. YAML
            accepts benchmark.environment. Values are frozen in the launch policy;
            credentials belong in child_secrets instead.
        model_configs: Inspect ModelConfig settings keyed by full model name.
            Child models and judge roles use these fixed settings.
        publisher: Publishing package, revealed only after findings submission.
            Defaults to latex; installed module:factory packages can replace it.
    """
    from ._findings import Findings, encoded, submit_findings
    from ._publishing import PhaseTools, load_publisher, preflight_publisher

    publisher = publisher or "latex"
    selected_publisher = load_publisher(str(publisher))
    box = box or "docker"
    hawk_api_url = hawk_api_url or os.environ.get("HAWK_API_URL")
    interactive = bool(interactive)
    task_package_pins = list(task_package_pins or [])
    investigator_image = str(investigator_image or INVESTIGATOR_IMAGE)
    auditor_image = str(auditor_image or AUDITOR_IMAGE)
    permissions = Permissions.model_validate(policy or {})
    benchmark_environment = validate_environment(benchmark_environment or {})
    for name, value in benchmark_environment.items():
        if os.environ.get(name) != value:
            raise ValueError(
                f"benchmark_environment declares {name!r}; set the same value in the "
                "auditor runner environment so task loading uses the child configuration"
            )
    requested_configs: dict[str, dict[str, Any]] = model_configs or {}
    configured = {}
    for name, value in requested_configs.items():
        parsed = ModelConfig.model_validate({"model": name, **value})
        if parsed.model != name:
            raise ValueError("model_configs keys must match each config's model")
        if "api_key" in parsed.args or any(key.startswith("aws_") for key in parsed.args):
            raise ValueError("model credentials belong in the environment or child_secrets")
        configured[name] = parsed.model_dump(mode="json", exclude_none=True)
    model_configs = configured
    child_secrets = list(child_secrets or [])
    if child_secrets and runs != "hawk":
        raise ValueError("child_secrets require runs='hawk'")
    for name in child_secrets:
        if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
            raise ValueError("child_secrets must contain environment variable names")
        if not os.environ.get(name):
            raise ValueError(
                f"declared child secret {name!r} is missing from the runner environment"
            )
    task_args = json.loads(canonical(task_args or {}))
    credential_mode = resolve_credential_mode(runs, credential_mode)
    # the models are the policy's alone, written by the audit's author: a model's name
    # depends on where it runs (on Hawk an OpenRouter model is
    # `openai/openrouter/<org>/<model>`, FRICTION 89), so there are no defaults
    models = permissions.models
    if credential_mode == "user" and runs == "hawk":
        logger.warning(USER_CHILDREN_ON_HAWK)
    if isinstance(investigation_samples, str) and investigation_samples != "all":
        raise ValueError("investigation_samples must be a list of IDs or 'all'")
    if investigation_samples is not None and not isinstance(investigation_samples, (str, list)):
        raise ValueError("investigation_samples must be a list of IDs or 'all'")
    from ._verdicts import investigation_items

    permitted_checks = tuple(s.name for s in investigation_items(checks))
    if not permitted_checks:
        raise ValueError("an audit needs at least one permitted check")

    if runs not in (None, "hawk", "local"):
        raise ValueError("runs must be 'hawk', 'local' or None")
    if box not in ("docker", "k8s"):
        raise ValueError("box must be 'docker' or 'k8s'")
    if runs == "hawk" and not hawk_api_url:
        raise ValueError("Hawk jobs need hawk_api_url (or HAWK_API_URL)")
    if not repo:
        raise ValueError("audit needs a repo: the benchmark to audit")
    skill_paths = [str(ASSETS / "skills" / name) for name in AUDIT_SKILLS] + [
        str(SKILLS / name) for name in (*LOG_SKILLS, *SUPPORT_SKILLS)
    ]
    for path in extra_skills or []:
        resolved = Path(path).expanduser().resolve()
        if not (resolved / "SKILL.md").is_file():
            raise ValueError(f"Expected a skill directory containing SKILL.md: {path}")
        skill_paths.append(str(resolved))

    # what can be worked out is worked out: the investigated repository already says which
    # commit it is, which directory the task lives in and which paper it comes from
    local_repo = Path(repo).expanduser()
    if local_repo.is_dir():
        target_task = target_task or only_task(local_repo)
        paths = paths or paths_from_metadata(local_repo, target_task)
        paper = paper or paper_from_metadata(local_repo, target_task)
    # somewhere every machine running this can write, and a person can find
    output_dir = output_dir or str(Path.home() / ".inspect-audit" / "audits")
    root = prepare_workspace(
        repo,
        revision,
        paths,
        logs or [],
        paper,
        docs or [],
        overview or "",
        target_task,
        output_dir,
    )

    (root / "work/findings.json").write_bytes(encoded(Findings().model_dump(mode="json")))
    (root / "work/findings.schema.json").write_text(
        json.dumps(Findings.model_json_schema(), indent=2)
    )
    seed_path = root / "inputs" / "seed.json"
    seed = json.loads(seed_path.read_text())
    if runs:
        if not target_task:
            raise ValueError("jobs run the investigated task: name it with target_task")
        task_package = task_package or (
            git_package_spec(local_repo, revision)
            if local_repo.is_dir()
            else f"git+{repo.removesuffix('.git')}@{revision}"
            if revision
            else None
        )
        investigation_package = investigation_package or own_package_spec()
        # only Hawk installs packages in its jobs; a local job runs in this machine's
        # environment and never reads them (FRICTION 79c)
        if runs == "hawk" and (not task_package or not investigation_package):
            raise ValueError("declare task_package and investigation_package for Hawk jobs")
        task_package = task_package or ""
        investigation_package = investigation_package or ""
    supplied = [str(e["remote"]) for e in seed["logs"] if e.get("remote")]
    if runs == "local":
        supplied += [str(e["source"]) for e in seed["logs"] if not e.get("remote")]
    # Keep a value snapshot, never a reference to the caller's configuration dict.
    resolved = locals().copy()
    configuration = {key: resolved[key] for key in inspect_module.signature(audit).parameters}
    configuration["resolved_source"] = {key: seed.get(key) for key in ("repo", "revision", "paths")}
    launch_policy = LaunchPolicy(
        task=target_task,
        task_package=task_package,
        task_pins=tuple(task_package_pins),
        investigation_package=investigation_package,
        investigator_image=investigator_image,
        model_configs_json=canonical(model_configs),
        models=models,
        samples=tuple(investigation_samples) if isinstance(investigation_samples, list) else None,
        checks=permitted_checks,
        task_args_json=canonical(task_args),
        environment_json=canonical(benchmark_environment),
        source=source,
        supplied_logs=tuple(sorted(set(supplied))),
        limits=permissions.limits,
        credential_mode=credential_mode,
        configuration_json=canonical(configuration),
    )
    snapshot = canonical(launch_policy.model_dump(mode="json"))
    (root / "policy.json").write_text(snapshot)
    (root / "inputs" / "policy.json").write_text(snapshot)
    seed["policy"] = {"path": "/inputs/policy.json", "sha256": launch_policy.digest}

    # where jobs run, built once: the job tools launch through it and `Spend` reads
    # every job's spend from it
    backend: Runs | None = None
    if runs == "hawk":
        backend = HawkRuns(
            hawk_api_url or "",
            credential_mode=launch_policy.credential_mode,
            secrets={name: os.environ[name] for name in child_secrets},
        )
    elif runs:
        # local runs outside the per-construction folder: a resumed run must find them
        backend = LocalRuns(Path(output_dir).expanduser().resolve() / "runs")
    # every model the audit may use, its own included
    try:
        from inspect_ai.model import get_model

        auditor_model = [str(get_model())]
    except ValueError:
        auditor_model = []  # built outside an eval: no model of its own
    require_models(
        [*auditor_model, *models.investigators, *models.subjects, *models.judges],
        backend,
        hawk_api_url,
    )
    spend = Spend(
        backend,
        launch_policy.limits.budget_usd,
        # the Hawk key whenever Hawk is in the picture and its address is known: jobs
        # on Hawk, or the auditor itself in a Hawk pod (FRICTION 79b; was only the first)
        hawk_api_url=hawk_api_url if (runs == "hawk" or box == "k8s") else None,
        # the auditor's own calls bill to OPENROUTER_API_KEY in a local box, as do local jobs
        openrouter=box == "docker" or runs == "local",
    )
    tools: list[Tool] = [
        # a shell dump is the one place the agent routinely wants more than the
        # 32KB default; everything else writes large results to files
        ToolDef(bash(timeout=300), max_output=128 * 1024).as_tool(),
        skill(skill_paths),
        budget(spend),
        view_image(),
        report_blocker(),
        register_view(target_task, launch_policy),
    ]
    inv = None
    if backend is not None:
        if target_task is None:
            raise RuntimeError("the investigated task was not resolved")
        inv = Audit(
            root=root,
            runs=backend,
            task=target_task,
            task_package=task_package or "",
            investigation_package=investigation_package or "",
            investigator_image=investigator_image,
            supplied=sorted(set(supplied)),
            authorizer=AuditAuthorizer(launch_policy),
            spend=spend,
            task_pins=task_package_pins,
            paper=paper,
        )
        tools += [run_eval(inv), send_investigators(inv), jobs(inv)]
        seed["jobs"] = {
            "runs": runs,
            "task": target_task,
            "investigator_models": list(models.investigators),
            "subject_models": list(models.subjects),
            "judge_models": list(models.judges),
            "supplied_logs": inv.supplied,
        }
    tools.append(submit_findings(root, inv, target_task, selected_publisher.validate))
    # Common shell/image/budget/blocker tools survive the handoff. Skill and job
    # capabilities are resolved per turn by Inspect, not by a second agent loop.
    common = [
        t for t in tools if ToolDef(t).name in ("bash", "view_image", "budget", "report_blocker")
    ]
    phase_tools = PhaseTools(
        root, common, [t for t in tools if t not in common], selected_publisher
    )
    seed_path.write_text(json.dumps(seed, indent=2))

    from ._audit_workspace import configure, install_benchmark, stage_inputs

    sandbox = configure(root, auditor_image, box_resources) if box == "k8s" else docker_box(root)
    if artifact_dir:
        (root / "artifacts.json").write_text(json.dumps({"artifact_dir": artifact_dir}))
    elif box == "k8s":
        logger.warning(
            "no artifact_dir: the published report stays on this host only, and on a "
            "Hawk runner it is lost when the pod ends"
        )

    async def on_continue(state: AgentState) -> bool | str:
        ensure_policy(launch_policy, store_as(PolicyStore), require_existing=True)
        return await _continue(state, interactive)

    @solver
    def begin_findings() -> Solver:
        async def solve(state: TaskState, generate: Generate) -> TaskState:
            from ._register import Register, save

            save(
                Register(
                    subject={
                        "repo": seed.get("repo"),
                        "revision": seed.get("revision"),
                        "task": target_task,
                    }
                ),
            )
            return state

        return solve

    async def on_resume(
        state: TaskState, attempt: Literal["initial", "resume", "resume_for_scoring"]
    ) -> None:
        ensure_policy(launch_policy, state.store_as(PolicyStore), require_existing=True)
        ensure_model(launch_policy, state.store_as(PolicyStore), str(state.model))

    async def cleanup(state: TaskState) -> None:
        # children do not outlive the audit that launched them
        if inv is not None:
            await inv.stop_unsettled(state.store_as(AuditRecord))

    return Task(
        setup=[
            preflight_publisher(selected_publisher),
            bind_policy(launch_policy),
            stage_inputs(root),
            *(
                [install_benchmark(launch_policy.box_install(), launch_policy.source)]
                if task_package
                else []
            ),
            begin_findings(),
        ],
        on_resume=on_resume,
        cleanup=cleanup,
        dataset=[
            Sample(
                id="audit",
                input=(
                    "Operator instructions, which take precedence over the default "
                    f"workflow below:\n{instructions}\n\n"
                    if instructions
                    else ""
                )
                + "Read /inputs/seed.json and invoke the auditing skill. Begin the "
                "audit autonomously.",
            )
        ],
        scorer=audit_outcome(),
        # the operator's budget, stated once in the policy, also bounds the auditor's own
        # calls (an eval set's `cost_limit`, if set, overrides it): FRICTION 100
        cost_limit=launch_policy.limits.budget_usd,
        solver=[
            as_solver(
                react(
                    name="auditor",
                    prompt=prompts.AUDIT,
                    submit=False,
                    tools=[phase_tools],
                    # an absolute threshold: 0.8 of a 1.05M-token context fired at ~840k,
                    # long after every turn had become expensive
                    compaction=CompactionSummary(threshold=COMPACTION_TOKENS),
                    truncation="auto",
                    on_continue=on_continue,
                )
            ),
            end_if_blocked(),
        ],
        sandbox=sandbox,
        # a crashed run resumes from its last checkpoint: the conversation, the box's
        # inputs and workspace, and the store's record of launched jobs
        checkpoint=CheckpointConfig(sandbox_paths={"default": ["/workspace", "/inputs"]}),
        # per-tool limits: bash above; the host tools summarise and write the rest to files
        config=GenerateConfig(max_tool_output=32 * 1024),
        token_limit=token_limit,
        working_limit=4 * 3600,
        metadata={
            "audit_dir": str(root),
            "interactive": interactive,
            "runs": runs,
            "box": box,
            "policy_sha256": launch_policy.digest,
        },
    )
