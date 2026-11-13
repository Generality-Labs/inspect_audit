"""Top-level audit agent, staged inputs and checkpointed child-job records."""

import asyncio
import errno
import inspect as inspect_module
import json
import os
import re
import shutil
import subprocess
import tarfile
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass, field
from logging import getLogger
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

import yaml
from inspect_ai import Task, task
from inspect_ai.agent import AgentState, as_solver, react
from inspect_ai.dataset import Sample
from inspect_ai.log import (
    EvalSampleSummary,
    list_eval_logs,
    read_eval_log_async,
    read_eval_log_sample_summaries_async,
)
from inspect_ai.model import CompactionSummary, GenerateConfig, ModelConfig, get_model
from inspect_ai.solver import Generate, Solver, TaskState, solver
from inspect_ai.tool import Tool, ToolDef, ToolError, bash, skill, tool
from inspect_ai.util import CheckpointConfig, sandbox, store_as

from . import prompts
from ._image import view_image
from ._log_sources import fetch_logs, is_remote_logs
from ._prices import UnpricedModelError, add_usage, cost_of, price_models, prices_for
from ._report import (
    AuditRecord,
    AuditState,
    audit_outcome,
    end_if_blocked,
    operator_turn,
    report_blocker,
)
from ._spend import Spend
from ._verdicts import LOG_SKILLS, SKILLS, SUPPORT_SKILLS
from .containers import (
    AUDITOR_DOCKERFILE,
    AUDITOR_IMAGE,
    AUDITOR_REQUIREMENTS,
    INVESTIGATOR_IMAGE,
)
from .dispatch import (
    FINISHED,
    HawkRuns,
    Job,
    JobKind,
    JobState,
    LocalRuns,
    Runs,
    validate_environment,
)
from .dispatch.packages import git_package_spec, own_package_spec
from .dispatch.policy import (
    USER_CHILDREN_ON_HAWK,
    AuditAuthorizer,
    Benchmark,
    Execution,
    InvestigationScope,
    LaunchPolicy,
    Models,
    Permissions,
    PolicyStore,
    bind_policy,
    canonical,
    ensure_model,
    ensure_policy,
)
from .dispatch.spend import key_balance

logger = getLogger(__name__)

ASSETS = Path(__file__).parent / "audit"

DEFAULT_INVESTIGATOR_IMAGE = INVESTIGATOR_IMAGE
DEFAULT_AUDITOR_IMAGE = AUDITOR_IMAGE

AUDIT_SKILLS = (
    "auditing",
    "running-jobs",
    "eval-validity-review",
    "investigate-dataset",
    "security-audit-eval",
    "check-trajectories-workflow",
    "read-eval-logs",
)
# compact the auditor's context once it passes this many tokens
COMPACTION_TOKENS = 200_000


def _child_prices(models: list[str]) -> dict[str, dict[str, float]]:
    """The prices a child job is handed; an unpriced model refuses the call."""
    try:
        return prices_for(models)
    except UnpricedModelError as ex:
        raise ToolError(str(ex)) from None


@dataclass(frozen=True)
class ModelMenu:
    """The models an audit may use, by the part each plays."""

    investigators: list[str]
    subjects: list[str]
    judges: list[str]


def _snapshot_repo(
    repo: str, revision: str | None, inputs: Path, paths: list[str] | None
) -> dict[str, object]:
    source = Path(repo).expanduser()
    if source.is_dir():
        commit = subprocess.check_output(
            [
                "git",
                "-C",
                str(source),
                "rev-parse",
                "--verify",
                f"{revision or 'HEAD'}^{{commit}}",
            ],
            text=True,
        ).strip()
        # Limit the snapshot to the declared paths.
        archive = inputs / "source.tar"
        subprocess.run(
            [
                "git",
                "-C",
                str(source),
                "archive",
                "--format=tar",
                f"--output={archive}",
                commit,
                *(["--", *paths] if paths else []),
            ],
            check=True,
        )
        tree = inputs / "source"
        tree.mkdir()
        with tarfile.open(archive) as tar:
            tar.extractall(tree, filter="data")
        archive.unlink()
        return {
            "repo": str(source.resolve()),
            "revision": commit,
            "paths": paths or ["."],
            "snapshot": "/inputs/source",
            "source": "committed snapshot; excludes working-tree changes and submodule contents",
        }
    if not repo.startswith("https://"):
        raise ValueError("repo must be a local Git repository or an HTTPS Git URL")
    return {
        "repo": repo,
        "revision": revision or "HEAD",
        "paths": paths or ["."],
        "snapshot": None,
        "source": "remote repository; clone it yourself into /workspace",
    }


def _fetch_paper(paper: str, inputs: Path) -> str:
    """Stage the paper under inputs/paper/: a local file, or a URL downloaded now.

    An arXiv abstract URL is turned into its PDF. A failed download keeps the URL
    in the seed so the agent can try itself, with the failure recorded.
    """
    from ._paper import paper_file

    (inputs / "paper").mkdir(exist_ok=True)
    if not Path(paper).expanduser().is_file() and not paper.startswith(("http://", "https://")):
        return paper
    try:
        name, data = paper_file(paper)
    except Exception as ex:  # network is optional at prep time
        logger.warning(f"could not download the paper from {paper}: {ex}")
        return paper
    (inputs / "paper" / name).write_bytes(data)
    return f"/inputs/paper/{name}"


def _stage_docs(docs: list[str], inputs: Path) -> list[str]:
    """Copy documentation trees into inputs/docs/<name>/."""
    staged: list[str] = []
    for source in docs:
        path = Path(source).expanduser().resolve()
        if not path.is_dir():
            raise ValueError(f"Docs directory does not exist: {source}")
        dest = inputs / "docs" / path.name
        shutil.copytree(
            path,
            dest,
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".git", "node_modules"),
        )
        staged.append(f"/inputs/docs/{path.name}")
    return staged


def _link_or_copy(src: Path, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.link(src, dest)
    except OSError as ex:
        if ex.errno != errno.EXDEV:
            raise
        shutil.copyfile(src, dest)


def _audit_file(config: str, passed: dict[str, Any]) -> dict[str, Any]:
    """Task settings from a YAML file, so an audit is a document, not a command.

    Every key is a parameter of this task, and an argument given on the command line
    wins over the file, so a saved audit can be re-run with one thing changed.
    """
    path = Path(config).expanduser()
    if not path.is_file():
        raise ValueError(f"no such audit file: {config}")
    loaded = yaml.safe_load(path.read_text()) or {}
    if not isinstance(loaded, dict):
        raise ValueError(f"{config} must be a mapping of task settings")
    # Structured launch sections resolve once, before the immutable policy is made.
    sections = {
        "benchmark": (
            Benchmark,
            {
                "task": "target_task",
                "package": "task_package",
                "environment": "benchmark_environment",
            },
        ),
        "investigation": (
            InvestigationScope,
            {"samples": "investigation_samples", "checks": "checks"},
        ),
        "execution": (Execution, {"backend": "runs"}),
    }
    for section, (model, names) in sections.items():
        if section not in loaded:
            continue
        values = model.model_validate(loaded.pop(section)).model_dump(
            mode="json", exclude_unset=True
        )
        for key, value in values.items():
            target = names.get(key, key)
            if target in loaded:
                raise ValueError(f"{config} declares {target} both directly and under {section}")
            loaded[target] = value
    signature = inspect_module.signature(audit)
    unknown = set(loaded) - set(signature.parameters) - {"config"}
    if unknown:
        raise ValueError(
            f"{config} sets things this task does not take: {sorted(unknown)}. "
            f"Available: {sorted(k for k in signature.parameters if k != 'config')}"
        )
    # a value given on the command line beats the file. Every overridable parameter
    # defaults to None, so "given" is "not None": passing the same value the default
    # would have used is still passing it, and a file cannot turn something back on
    return {
        key: value for key, value in loaded.items() if key != "config" and passed.get(key) is None
    }


def _only_task(source: Path) -> str | None:
    """The task, when the repository declares exactly one and nobody said which."""
    declared: set[str] = set()
    for path in sorted(source.rglob("*.py")):
        if any(part in {".git", "tests", "test", "build", ".venv"} for part in path.parts):
            continue
        try:
            text = path.read_text(errors="ignore")
        except OSError:
            continue
        for match in re.finditer(r"@task(?:\([^)]*\))?\s*\ndef\s+(\w+)", text):
            named = re.search(r'name\s*=\s*["\']([^"\']+)["\']', match.group(0))
            declared.add(named.group(1) if named else match.group(1))
    if len(declared) == 1:
        only = declared.pop()
        logger.info(f"investigating {only}: the only task {source} declares")
        return only
    return None


def paths_from_metadata(source: Path, target_task: str | None) -> list[str] | None:
    """Select the task directory, its local imports and package metadata."""
    from ._source import collect_source

    directory = _task_directory(source, target_task)
    if directory is None:
        return None
    # what the task's code imports, followed the way the investigator's source copy is
    base = source.resolve()
    chosen = {str(directory.relative_to(source))}
    starts = sorted(directory.rglob("*.py")) if directory.is_dir() else [directory]
    for start in starts:
        for file in collect_source(start.resolve()).files:
            if file.resolve().is_relative_to(base):
                chosen.add(str(file.resolve().relative_to(base)))
    for top in ("pyproject.toml", "README.md"):
        if (source / top).is_file():
            chosen.add(top)
    # a file inside a directory already named adds nothing to the snapshot
    directories = {c for c in chosen if (source / c).is_dir()}
    return sorted(c for c in chosen if not any(c.startswith(f"{d}/") for d in directories))


def _task_directory(source: Path, target_task: str | None) -> Path | None:
    """Find the task directory through eval.yaml or its @task declaration; return None if unknown."""
    if not target_task:
        return None
    name = target_task.split("/")[-1]
    for path in sorted(source.rglob("eval.yaml")):
        tasks = _safe_yaml(path).get("tasks")
        names = {
            str(entry.get("name", ""))
            for entry in (tasks if isinstance(tasks, list) else [])
            if isinstance(entry, dict)
        }
        if name in names or path.parent.name == name:
            return path.parent
    return _declaring_file(source, name)


# `@task` may name the task itself, or take the function's name. Both spellings, and
# the registry name may carry spaces where the function has underscores.
def _declaring_file(source: Path, name: str) -> Path | None:
    function = re.compile(
        rf"^\s*def {re.escape(name.replace(' ', '_').lower())}\s*\(", re.MULTILINE
    )
    declared = re.compile(rf"@task\([^)]*name\s*=\s*[\"']{re.escape(name)}[\"']", re.DOTALL)
    fallback: Path | None = None
    for path in sorted(source.rglob("*.py")):
        if any(part in {".git", "tests", "test", "build", ".venv"} for part in path.parts):
            continue
        try:
            text = path.read_text(errors="ignore")
        except OSError:
            continue
        if "@task" not in text:
            continue
        if declared.search(text):
            return path.parent
        if function.search(text) and fallback is None:
            fallback = path.parent
    return fallback


def _safe_yaml(path: Path) -> dict[str, object]:
    try:
        loaded = yaml.safe_load(path.read_text())
    except (yaml.YAMLError, OSError):
        return {}
    return loaded if isinstance(loaded, dict) else {}


def paper_from_metadata(source: Path, target_task: str | None) -> str | None:
    """The paper an eval names in its own metadata, when it names one."""
    if not target_task:
        return None
    directory = _task_directory(source, target_task)
    if directory is not None:
        arxiv = str(_safe_yaml(directory / "eval.yaml").get("arxiv") or "").strip()
        if arxiv:
            # several papers means the eval was revised; the last is the current one
            return arxiv.split(",")[-1].strip()
    return None


def prepare_workspace(
    repo: str,
    revision: str | None,
    paths: list[str] | None,
    logs: list[str],
    paper: str | None,
    docs: list[str],
    overview: str,
    target_task: str | None,
    output_dir: str,
) -> Path:
    """Stage the declared inputs on the host; the box gets a copy at the start."""
    root = Path(output_dir).expanduser().resolve() / uuid4().hex
    inputs = root / "inputs"
    work = root / "work"
    inputs.mkdir(parents=True)
    work.mkdir()
    seed: dict[str, object] = dict(_snapshot_repo(repo, revision, inputs, paths))
    log_index = []
    for index, source in enumerate(logs):
        # a hawk: source is downloaded now; it keeps its address, so a job can be
        # pointed at the same logs and a citation names where they live
        local = Path(fetch_logs(source)) if is_remote_logs(source) else None
        path = local or Path(source).expanduser().resolve()
        if not path.exists():
            raise ValueError(f"Log source does not exist: {source}")
        files = (
            [path]
            if path.is_file()
            else [
                Path(info.name.removeprefix("file://")).resolve()
                for info in list_eval_logs(str(path), recursive=True)
            ]
        )
        if not files:
            raise ValueError(f"No Inspect logs found: {source}")
        for file in files:
            relative = Path(str(index)) / (file.name if path.is_file() else file.relative_to(path))
            _link_or_copy(file, inputs / "logs" / relative)
            log_index.append(
                {
                    "source": f"{source.rstrip('/')}/{file.name}" if local else str(file),
                    "staged": f"/inputs/logs/{relative}",
                    "remote": source if local else None,
                }
            )
    staged_paper = _fetch_paper(paper, inputs) if paper else None
    staged_docs = _stage_docs(docs, inputs)
    seed.update(
        task=target_task,
        overview=overview,
        paper=staged_paper,
        docs=staged_docs,
        logs=log_index,
    )
    (inputs / "seed.json").write_text(json.dumps(seed, indent=2))
    from ._framework import stage_framework

    stage_framework(work / "analysis")
    (work / "report").mkdir()
    (work / "journal.md").write_text(
        "# Activity journal\n\nAppend actions and corrections with evidence references.\n"
    )
    return root


def docker_box(root: Path) -> tuple[str, str]:
    """The auditor's box on local Docker: built here, nothing mounted."""
    (root / "Dockerfile").write_text(AUDITOR_DOCKERFILE)
    (root / "auditor.requirements.txt").write_text(AUDITOR_REQUIREMENTS)
    (root / ".dockerignore").write_text("*\n!Dockerfile\n!auditor.requirements.txt\n")
    compose = {
        "services": {
            "default": {
                "build": {"context": "."},
                "command": "sleep infinity",
                "init": True,
                "working_dir": "/workspace",
            }
        }
    }
    (root / "compose.yaml").write_text(yaml.safe_dump(compose))
    return "docker", str(root / "compose.yaml")


@tool(name="budget")
def budget(spend: Spend, hawk_api_url: str | None, openrouter: bool) -> Tool:
    async def execute() -> str:
        """What this audit has spent so far, job by job, and what is left.

        Spent: your own calls and every job's spend so far, running or finished, from
        its token counts. No job is capped and nothing is reserved: decide what the
        work is worth from these costs, watch this as you go, and stop or stop jobs when
        it is not. The model key's balance, below, counts everything that bills to it.
        """
        lines = await spend.report()
        lines += await key_balance(
            hawk_api_url, openrouter and str(get_model()).startswith("openrouter/")
        )
        return "\n".join(lines)

    return execute


@contextmanager
def _platform(what: str) -> Generator[None]:
    """The platform refusing or failing a call is the auditor's to work around, not a crash."""
    try:
        yield
    except ToolError:
        raise
    except Exception as ex:
        raise ToolError(f"{what} failed: {type(ex).__name__}: {ex}") from ex


@solver
def check_models(runs: Runs | None, policy: LaunchPolicy) -> Solver:
    """Refuse the audit before it starts when its policy names models the platform won't serve.

    Otherwise the first job naming one is refused at submission, after the audit has
    spent (FRICTION 53).
    """

    async def solve(state: TaskState, generate: Generate) -> TaskState:
        unservable = getattr(runs, "unservable", None)
        m = policy.models
        named = [*m.investigators, *m.subjects, *m.judges]
        if unservable is not None and named and (missing := await unservable(named)):
            raise RuntimeError(
                "The policy names models Hawk's proxy does not serve under those names: "
                f"{', '.join(missing)}. Jobs naming them would be refused. Add them "
                "(hawk proxy models add) or take them out of the policy."
            )
        return state

    return solve


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

    def record(self) -> AuditRecord:
        return store_as(AuditRecord)

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
        with _platform(f"submitting {job.label!r}"):
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
    from ._register import load, started

    current = load()
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
        item_cost_limit: float | None,
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
                recorded attempt is scored again (1 to 10; disagreeing epochs are judge noise).
            rescore: Recorded attempts to score again instead of making new ones: labels
                of your jobs, or supplied log sources; [] for fresh attempts.
            code: A folder in your box holding the files you changed, each at its path
                inside the installed package (e.g. inspect_evals/simpleqa/simpleqa.py);
                every other file is the original. Null for the unmodified benchmark.
            packages: Exact versions to install instead of the benchmark's own, as
                name==version (e.g. ["numpy==1.26.4"]; "inspect-ai==..." changes Inspect,
                except with `code`); [] for the benchmark's. Hawk runs only.
            item_cost_limit: Dollars one rescored attempt may spend; null for fresh attempts.
            note: Why you are running this.
        """
        policy = inv.authorizer.policy
        judges = {str(k): str(v) for k, v in json_object(judge, "judge").items()}
        files, diff = await _variant_files(inv, code) if code else (None, None)
        args = json.loads(policy.task_args_json)
        if rescore:
            if models:
                raise ToolError("rescoring makes no new attempts: models must be []")
            if item_cost_limit is None:
                raise ToolError("rescoring needs item_cost_limit: dollars per rescored attempt")
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
                    "cost_limit": item_cost_limit,
                    "prices": _child_prices(list(judges.values()) or ["mockllm/model"]),
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
        item_cost_limit: float,
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
            item_cost_limit: Dollars one investigated item may spend.
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
                "cost_limit": item_cost_limit,
                "prices": _child_prices([investigator, *judges.values()]),
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
        with _platform(f"jobs {action} {label!r}"):
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
        while (status := await inv.runs.status(run_id)) not in (*FINISHED, JobState.STUCK):
            if asyncio.get_running_loop().time() > deadline:
                break
            await asyncio.sleep(30)
        return f"{label}: {status}"
    if action == "log":
        return await inv.runs.runner_log(run_id)
    if action == "stop":
        await inv.runs.stop(run_id)
        return (
            f"stop requested for {label}; wait for it to finish before collecting. "
            "Completed samples remain in its logs."
        )
    if action == "collect":
        from ._audit_workspace import copy_into_box

        status = await inv.runs.status(run_id)
        if status not in FINISHED:
            raise ToolError(f"{label} is {status}; wait for it before collecting")
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
            lines.append(await _import_investigations(inv, files, job["address"]))
        record = inv.record()
        record.jobs = [{**j, "collected": True} if j["label"] == label else j for j in record.jobs]
        return f"{label}: {status}; collected to /inputs/jobs/{label}/\n" + "\n".join(lines)
    raise ToolError("action must be list, status, wait, collect, log or stop")


async def start_register(task: str | None, policy: LaunchPolicy) -> int:
    """Lay out the register from the launch policy; returns the number of items.

    Every permitted item of the benchmark, with an empty cell per policy check.
    """
    from inspect_ai.util import registry_create

    from ._register import Register, load, save, started
    from ._task import numbered
    from ._verdicts import investigation_items

    if not task:
        raise ToolError("the register needs the investigated task: the operator sets target_task")
    if started(load()):
        raise ToolError("the register is already started; it keeps one population")
    ensure_policy(policy, store_as(PolicyStore), require_existing=True)
    args = json.loads(policy.task_args_json)
    try:
        skills = investigation_items(list(policy.checks))
    except ValueError as ex:
        raise ToolError(str(ex)) from ex
    try:
        their = registry_create("task", task, **args)
    except Exception as ex:
        raise ToolError(
            f"cannot load {task} here to read its dataset ({type(ex).__name__}: {ex}). "
            "The benchmark's package must be installed beside inspect_audit."
        ) from ex
    dataset, positional = numbered(their)
    if policy.samples is not None:
        known = {str(s.id) for s in dataset}
        if set(policy.samples) - known:
            raise ToolError("launch policy selects sample IDs absent from the benchmark")
        dataset = [s for s in dataset if str(s.id) in policy.samples]
    register = Register(
        subject={
            **load().subject,
            "task": task,
            "task_args": args,
            "dataset": getattr(their.dataset, "name", None),
            "ids": "positional" if positional else "declared",
        },
        checks=[s.name for s in skills],
        samples={str(s.id): {c.name: [] for c in skills} for s in dataset},
    )
    await save(register)
    return len(dataset)


@tool(name="register")
def register_view(task: str | None = None, policy: LaunchPolicy | None = None) -> Tool:
    async def execute(view: str, check: str | None) -> str:
        """The register: every permitted item against every check, and where each stands.

        It starts itself from the launch policy on first use. Cells fill as investigation
        jobs are collected and as you review; an empty cell is work not yet done.

        Args:
            view: "summary" (counts per check), "flagged" (an investigator flagged a
                problem you have not reviewed), "unresolved" (the standing take is
                unresolved), "missing" (no take yet) or "disputed" (takes disagree).
            check: Only this check; null for all.
        """
        from ._register import load, select, started
        from ._verdicts import investigation_items

        if view not in ("summary", "flagged", "unresolved", "missing", "disputed"):
            raise ToolError("view is summary, flagged, unresolved, missing or disputed")
        if not started(load()):
            if policy is None:
                raise ToolError("no register: this audit has no launch policy to start one from")
            await start_register(task, policy)
        current = load()
        skills = {s.name: s for s in investigation_items(current.checks)}
        from ._findings import DRAFT, Findings
        from ._register import apply_reviews

        try:
            draft = Findings.model_validate_json(await sandbox().read_file(DRAFT))
            apply_reviews(current, draft.auditor.reviews, skills)
        except (ValueError, OSError) as ex:
            raise ToolError(f"Cannot read draft reviews: {ex}") from ex
        rows = select(current, view, skills, check)  # type: ignore[arg-type]
        text = json.dumps(rows, indent=1)
        if len(text) > 12_000:
            text = text[:12_000] + f"\n... (read {DRAFT} for the rest)"
        return text

    return execute


async def _import_investigations(inv: Audit, files: list[Path], address: str) -> str:
    """An investigation job's verdicts into the register, starting it if need be."""
    from ._register import import_investigation_log, load, save, started
    from ._verdicts import investigation_items

    if not started(load()):
        await start_register(inv.task, inv.authorizer.policy)
    current = load()
    skills = {s.name: s for s in investigation_items()}
    flags = {g for s in skills.values() for g in s.flags}
    added: list[str] = []
    try:
        for file in files:
            added += import_investigation_log(
                current, await read_eval_log_async(str(file)), address, skills
            )
    except ValueError as ex:
        raise ToolError(f"cannot add this investigation to the register: {ex}") from ex
    await save(current)
    flagged = sum(1 for a in added if a.split("=")[-1] in flags)
    return f"  register: {len(added)} investigator take(s) added, {flagged} flagged"


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
    config: str | None = None,
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
    investigator_models: list[str] | None = None,
    subject_models: list[str] | None = None,
    judge_models: list[str] | None = None,
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
        config: YAML file setting any of these arguments, so an audit can be a
            document rather than a command line. Anything also passed with -T wins.
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
        investigator_models: Full Inspect model names an investigation job may use as its investigator.
        subject_models: Full Inspect model names a benchmark job may evaluate.
        judge_models: Full Inspect model names the benchmark's own judges may be. Declare the
            judges the investigated logs record; none by default.
        auditor_image: Published auditor sandbox image for Hawk.
        box_resources: Kubernetes `resources` (requests/limits) for the auditor's box on
            k8s; None uses the deployment's sandbox default.
        artifact_dir: Where the published report is also saved, under this sample:
            on Hawk, the job's S3 artifacts directory. None keeps it on the host only.
        instructions: Operator instructions and scope, separate from benchmark background.
        policy: Frozen model permissions, declared log inputs and job limits.
            YAML also accepts benchmark, investigation and execution sections. Permission
            changes on resume are refused; start a new audit instead.
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
    # the file is read before anything else is decided: an argument is "given" only
    # when it is not None, so a value equal to a default is still an override
    settings = _audit_file(config, locals()) if config else {}
    from ._findings import Findings, encoded, submit_findings
    from ._publishing import PhaseTools, load_publisher, preflight_publisher

    publisher = settings.get("publisher", publisher) or "latex"
    selected_publisher = load_publisher(str(publisher))
    repo = settings.get("repo", repo)
    logs = settings.get("logs", logs)
    paper = settings.get("paper", paper)
    docs = settings.get("docs", docs)
    overview = settings.get("overview", overview)
    target_task = settings.get("target_task", target_task)
    revision = settings.get("revision", revision)
    paths = settings.get("paths", paths)
    output_dir = settings.get("output_dir", output_dir)
    runs = settings.get("runs", runs)
    box = settings.get("box", box) or "docker"
    hawk_api_url = settings.get("hawk_api_url", hawk_api_url) or os.environ.get("HAWK_API_URL")
    token_limit = settings.get("token_limit", token_limit)
    interactive = bool(settings.get("interactive", interactive))
    extra_skills = settings.get("extra_skills", extra_skills)
    investigation_package = settings.get("investigation_package", investigation_package)
    task_package = settings.get("task_package", task_package)
    task_package_pins = list(settings.get("task_package_pins", task_package_pins) or [])
    investigator_image = str(
        settings.get("investigator_image", investigator_image) or DEFAULT_INVESTIGATOR_IMAGE
    )
    # no default models: a model's name depends on where it runs (on Hawk an OpenRouter
    # model is `openai/openrouter/<org>/<model>`, FRICTION 89), so the audit's author
    # writes them
    menu = ModelMenu(
        investigators=list(settings.get("investigator_models", investigator_models) or []),
        subjects=list(settings.get("subject_models", subject_models) or []),
        judges=list(settings.get("judge_models", judge_models) or []),
    )
    auditor_image = str(settings.get("auditor_image", auditor_image) or DEFAULT_AUDITOR_IMAGE)
    box_resources = settings.get("box_resources", box_resources)
    artifact_dir = settings.get("artifact_dir", artifact_dir)
    instructions = settings.get("instructions", instructions)
    policy = settings.get("policy", policy)
    permissions = Permissions.model_validate(policy or {})
    benchmark_environment = validate_environment(
        settings.get("benchmark_environment", benchmark_environment) or {}
    )
    for name, value in benchmark_environment.items():
        if os.environ.get(name) != value:
            raise ValueError(
                f"benchmark_environment declares {name!r}; set the same value in the "
                "auditor runner environment so task loading uses the child configuration"
            )
    requested_configs: dict[str, dict[str, Any]] = (
        settings.get("model_configs", model_configs) or {}
    )
    configured = {}
    for name, value in requested_configs.items():
        parsed = ModelConfig.model_validate({"model": name, **value})
        if parsed.model != name:
            raise ValueError("model_configs keys must match each config's model")
        if "api_key" in parsed.args or any(key.startswith("aws_") for key in parsed.args):
            raise ValueError("model credentials belong in the environment or child_secrets")
        configured[name] = parsed.model_dump(mode="json", exclude_none=True)
    model_configs = configured
    child_secrets = list(settings.get("child_secrets", child_secrets) or [])
    if child_secrets and runs != "hawk":
        raise ValueError("child_secrets require runs='hawk'")
    for name in child_secrets:
        if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
            raise ValueError("child_secrets must contain environment variable names")
        if not os.environ.get(name):
            raise ValueError(
                f"declared child secret {name!r} is missing from the runner environment"
            )
    task_args = json.loads(canonical(settings.get("task_args", task_args) or {}))
    investigation_samples = settings.get("investigation_samples", investigation_samples)
    checks = settings.get("checks", checks)
    source = settings.get("source", source)
    # Hawk child jobs are restricted unless the audit states otherwise (FRICTION 73, 93);
    # local jobs run with the operator's own credentials
    credential_mode = settings.get("credential_mode", credential_mode) or (
        "restricted" if runs == "hawk" else "user"
    )
    if policy is not None:
        if any(v is not None for v in (investigator_models, subject_models, judge_models)) or any(
            key in settings for key in ("investigator_models", "subject_models", "judge_models")
        ):
            raise ValueError(
                "declare models under policy.models or legacy model arguments, not both"
            )
        menu = ModelMenu(
            investigators=list(permissions.models.investigators),
            subjects=list(permissions.models.subjects),
            judges=list(permissions.models.judges),
        )
        declared_logs = list(permissions.inputs.logs)
        if logs is not None and logs != declared_logs:
            raise ValueError("logs and policy.inputs.logs disagree")
        logs = declared_logs
    # every model the audit may use, its own included, must be priced where it runs, or
    # the audit does not start (FRICTION 84, 89); the auditor's price is registered for
    # its run-level cost_limit
    try:
        from inspect_ai.model import get_model

        auditor_model = [str(get_model())]
    except ValueError:
        auditor_model = []  # built outside an eval: no model of its own
    price_models([*auditor_model, *menu.investigators, *menu.subjects, *menu.judges])
    if credential_mode not in ("restricted", "user"):
        raise ValueError("credential_mode must be 'restricted' or 'user'")
    if credential_mode == "restricted" and runs != "hawk":
        raise ValueError("restricted credentials require runs='hawk'")
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
        raise ValueError("audit needs a repo: the benchmark to audit, or a config naming one")
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
        target_task = target_task or _only_task(local_repo)
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
    configuration = {
        key: resolved[key] for key in inspect_module.signature(audit).parameters if key != "config"
    }
    configuration["resolved_source"] = {key: seed.get(key) for key in ("repo", "revision", "paths")}
    launch_policy = LaunchPolicy(
        task=target_task,
        task_package=task_package,
        task_pins=tuple(task_package_pins),
        investigation_package=investigation_package,
        investigator_image=investigator_image,
        model_configs_json=canonical(model_configs),
        models=Models(
            auditor=permissions.models.auditor,
            investigators=tuple(menu.investigators),
            subjects=tuple(menu.subjects),
            judges=tuple(menu.judges),
        ),
        samples=tuple(investigation_samples) if isinstance(investigation_samples, list) else None,
        checks=permitted_checks,
        task_args_json=canonical(task_args),
        environment_json=canonical(benchmark_environment),
        source=source,
        supplied_logs=tuple(sorted(set(supplied))),
        limits=permissions.limits,
        credential_mode="restricted" if credential_mode == "restricted" else "user",
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
    spend = Spend(backend, launch_policy.limits.budget_usd)
    tools: list[Tool] = [
        # a shell dump is the one place the agent routinely wants more than the
        # 32KB default; everything else writes large results to files
        ToolDef(bash(timeout=300), max_output=128 * 1024).as_tool(),
        skill(skill_paths),
        # the agent's own calls bill to the Hawk user's key in a pod, to
        # OPENROUTER_API_KEY in a local box; jobs to whichever their backend uses
        budget(
            spend,
            # the Hawk key whenever Hawk is in the picture and its address is known: jobs
            # on Hawk, or the auditor itself in a Hawk pod (FRICTION 79b; was only the first)
            hawk_api_url if (runs == "hawk" or box == "k8s") else None,
            openrouter=box == "docker" or runs == "local",
        ),
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
            "investigator_models": menu.investigators,
            "subject_models": menu.subjects,
            "judge_models": menu.judges,
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

            await save(
                Register(
                    subject={
                        "repo": seed.get("repo"),
                        "revision": seed.get("revision"),
                        "task": target_task,
                    }
                )
            )
            return state

        return solve

    async def on_resume(
        state: TaskState, attempt: Literal["initial", "resume", "resume_for_scoring"]
    ) -> None:
        ensure_policy(launch_policy, state.store_as(PolicyStore), require_existing=True)
        ensure_model(launch_policy, state.store_as(PolicyStore), str(state.model))

    return Task(
        setup=[
            preflight_publisher(selected_publisher),
            bind_policy(launch_policy),
            check_models(inv.runs if inv else None, launch_policy),
            stage_inputs(root),
            *(
                [install_benchmark(launch_policy.box_install(), launch_policy.source)]
                if task_package
                else []
            ),
            begin_findings(),
        ],
        on_resume=on_resume,
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
