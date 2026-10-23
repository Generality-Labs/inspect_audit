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
import urllib.request
from dataclasses import dataclass, field
from importlib.metadata import version
from logging import getLogger
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

import yaml
from inspect_ai import Task, task
from inspect_ai.agent import AgentState, react
from inspect_ai.dataset import Sample
from inspect_ai.log import list_eval_logs, read_eval_log_sample_summaries
from inspect_ai.model import CompactionSummary, GenerateConfig, ModelConfig, get_model
from inspect_ai.solver import TaskState
from inspect_ai.tool import Tool, ToolDef, ToolError, bash, skill, tool
from inspect_ai.util import CheckpointConfig, sandbox, store_as

from . import prompts
from ._image import view_image
from ._log_sources import fetch_logs, is_remote_logs
from ._prices import prices_for_models, register_prices
from ._report import (
    AuditRecord,
    AuditState,
    audit_outcome,
    operator_turn,
    report_blocker,
)
from ._verdicts import LOG_SKILLS, SKILLS, SUPPORT_SKILLS
from .containers import AUDITOR_IMAGE, INVESTIGATOR_IMAGE
from .dispatch import FINISHED, STUCK, HawkRuns, Job, LocalRuns, Runs, validate_environment
from .dispatch.packages import git_package_spec, own_package_spec
from .dispatch.policy import (
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


@dataclass(frozen=True)
class ModelMenu:
    """The models an audit may use, by the part each plays."""

    investigators: list[str]
    subjects: list[str]
    judges: list[str]

    @property
    def all(self) -> list[str]:
        return list(dict.fromkeys([*self.investigators, *self.subjects, *self.judges]))


DEFAULT_WORKERS = [
    "openrouter/openai/gpt-5.6-luna",
    "openrouter/google/gemini-3.6-flash",
    "openrouter/openai/gpt-5.6-sol",
    "openrouter/openai/gpt-5.6-terra",
    "openrouter/openai/gpt-6-astra",
]


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
    local = Path(paper).expanduser()
    (inputs / "paper").mkdir(exist_ok=True)
    if local.is_file():
        shutil.copyfile(local.resolve(), inputs / "paper" / local.name)
        return f"/inputs/paper/{local.name}"
    if not paper.startswith(("http://", "https://")):
        return paper
    url = re.sub(r"arxiv\.org/abs/([^v?#]+)(v\d+)?", r"arxiv.org/pdf/\1\2", paper)
    name = Path(url.split("?", 1)[0].rstrip("/")).name or "paper"
    if not name.lower().endswith(".pdf"):
        name += ".pdf"
    try:
        request = urllib.request.Request(url, headers={"User-Agent": "inspect_audit"})
        with urllib.request.urlopen(request, timeout=60) as response:
            data = response.read()
    except Exception as ex:  # network is optional at prep time
        logger.warning(f"could not download the paper from {url}: {ex}")
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
    (root / "Dockerfile").write_text(
        (ASSETS / "Dockerfile").read_text().replace("INSPECT_VERSION", version("inspect-ai"))
    )
    (root / ".dockerignore").write_text("*\n!Dockerfile\n")
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
def budget(hawk_api_url: str | None, openrouter: bool) -> Tool:
    async def execute() -> str:
        """Read the configured model key's remaining allowance.

        This covers all calls only when the auditor and jobs share that key.
        It is not an attributed audit spend total. Calls using the key fail
        when its provider-enforced allowance is exhausted.
        """
        return "\n".join(
            await key_balance(
                hawk_api_url, openrouter and str(get_model()).startswith("openrouter/")
            )
        )

    return execute


@dataclass
class Audit:
    """What the job tools need that the agent does not hold."""

    root: Path
    runs: Runs
    where: str  # "hawk" or "local"
    task: str  # the investigated task's registry name
    task_package: str
    investigation_package: str
    investigator_image: str
    menu: ModelMenu
    supplied: list[str]  # log sources the operator gave, as addresses a job can read
    authorizer: AuditAuthorizer | None = None
    launch_lock: asyncio.Lock = field(default_factory=asyncio.Lock, init=False, repr=False)

    def __post_init__(self) -> None:
        if self.authorizer is None:
            from ._verdicts import investigation_items

            self.authorizer = AuditAuthorizer(
                LaunchPolicy(
                    task=self.task,
                    task_package=self.task_package,
                    investigation_package=self.investigation_package,
                    investigator_image=self.investigator_image,
                    models=Models(
                        investigators=tuple(self.menu.investigators),
                        subjects=tuple(self.menu.subjects),
                        judges=tuple(self.menu.judges),
                    ),
                    checks=tuple(s.name for s in investigation_items()),
                    supplied_logs=tuple(self.supplied),
                    configuration_json="{}",
                )
            )

    def record(self) -> AuditRecord:
        return store_as(AuditRecord)

    def job(self, label: str) -> dict[str, Any]:
        found = [j for j in self.record().jobs if j["label"] == label]
        if not found:
            raise ToolError(f"no job {label!r}; jobs(action='list') shows them")
        return found[0]

    async def launch(self, job: Job, kind: str, note: str) -> str:
        async with self.launch_lock:
            from ._findings import require_audit

            require_audit()
            return await self._launch(job, kind, note)

    async def _launch(self, job: Job, kind: str, note: str) -> str:
        if self.authorizer is None:
            raise RuntimeError("audit has no launch authorizer")
        policy = self.authorizer.policy
        approved = self.authorizer.authorise(
            job, kind, {str(j["address"]) for j in self.record().jobs}
        )
        # This object is made at launch; sandbox policy files are never read here.
        if policy.configuration_json != "{}":
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
        run_id = await self.runs.submit(approved)
        address = self.runs.address(run_id)
        # assigned back, not appended in place: the store records an assignment
        record = self.record()
        record.jobs = [
            *record.jobs,
            {
                "label": job.label,
                "kind": kind,
                "run_id": run_id,
                "address": address,
                "note": note,
                "policy_sha256": policy.digest,
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


@tool
def run_benchmark(inv: Audit) -> Tool:
    async def execute(
        label: str,
        models: list[str],
        sample_ids: list[str] | None,
        sample_range: list[int] | None,
        epochs: int,
        task_args: str,
        model_roles: str,
        note: str,
    ) -> str:
        """Run the investigated benchmark: fresh attempts by the models you name.

        Args:
            label: A short name for this job, unique in the audit.
            models: Full Inspect model names from the subject list.
            sample_ids: The benchmark's own sample ids to run, when its samples have
                ids; null for none.
            sample_range: [first, last] positions in the dataset, counting from 1,
                inclusive: how to select samples that have no ids of their own (they
                are numbered by position). Null for none. Give at most one of
                sample_ids and sample_range; neither runs every sample.
            epochs: Attempts per sample per model.
            task_args: The task's arguments as a JSON object, exactly as the
                benchmark takes them; "{}" for its defaults. A model named in one must
                be a permitted judge; arguments that set a sandbox,
                image, path or file are refused.
            model_roles: Role name to model for the benchmark's own judges, as a JSON
                object, from the judge list; "{}" when it has none.
            note: Why you are running this.
        """
        args = json_object(task_args, "task_args")
        roles = {str(k): str(v) for k, v in json_object(model_roles, "model_roles").items()}
        if sample_ids is not None and sample_range is not None:
            raise ToolError("give sample_ids or sample_range, not both")
        if sample_range is not None and not (
            len(sample_range) == 2 and 1 <= sample_range[0] <= sample_range[1]
        ):
            raise ToolError("sample_range is [first, last], counting from 1, first <= last")
        job = Job(
            label=label,
            task=inv.task,
            package=inv.task_package,
            models=models,
            task_args=args,
            model_roles=roles,
            sample_ids=sample_ids,
            sample_range=(sample_range[0], sample_range[1]) if sample_range else None,
            epochs=epochs,
        )
        return await inv.launch(job, "benchmark", note)

    return execute


@tool
def run_investigation(inv: Audit) -> Tool:
    async def execute(
        label: str,
        samples: list[str],
        checks: list[str],
        logs: list[str],
        investigator_model: str,
        source: str,
        item_cost_limit: float,
        task_args: str,
        judge: str,
        note: str,
    ) -> str:
        """Investigation benchmark items with inspect_audit's investigator, one sample per item.

        Args:
            label: A short name for this job, unique in the audit.
            samples: The benchmark's sample ids to investigation.
            checks: Checks to run, e.g. ["gold-answer", "red-teaming"].
            logs: Recorded attempts to investigation against: labels of benchmark jobs you
                ran, or supplied log sources from the seed; [] for none.
            investigator_model: The investigator, a full Inspect model name from the investigator list.
            source: The Python module that defines the benchmark's task, e.g.
                inspect_evals.simpleqa.simpleqa: its code is what the investigator reads.
            item_cost_limit: Dollars one investigated item may spend.
            task_args: The benchmark's arguments as a JSON object, exactly as the logs
                ran it, under the same rules as run_benchmark's.
            judge: Role name to model for the benchmark's judge, as a JSON object, from
                the judge list. With logs, it must be the model the logs record; "{}"
                for none.
            note: Why you are running this.
        """
        args = json_object(task_args, "task_args")
        judges = {str(k): str(v) for k, v in json_object(judge, "judge").items()}
        if not re.fullmatch(r"[A-Za-z_]\w*(\.[A-Za-z_]\w*)*", source):
            raise ToolError(f"source must be a module name like pkg.mod.task, not {source!r}")
        addresses = []
        for name in logs:
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
        try:
            prices = prices_for_models([investigator_model, *judges.values()])
        except Exception as ex:
            raise ToolError(f"cannot price the investigator for its cost limit: {ex}") from ex
        job = Job(
            label=label,
            task="inspect_audit/investigation",
            package=inv.investigation_package,
            extra_packages=[inv.task_package],
            models=[investigator_model],
            task_args={
                "task": inv.task,
                "task_args": args,
                "samples": samples,
                "checks": checks,
                "source": source,
                "logs": addresses or None,
                # {} is "their grader calls no model", not "unknown": pass it as given
                "judge": judges,
                "investigator_image": inv.investigator_image,
                "cost_limit": item_cost_limit,
                "model_prices": prices,
            },
        )
        return await inv.launch(job, "investigation", note)

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
        run_id = job["run_id"]
        if action == "status":
            return f"{label}: {await inv.runs.status(run_id)}"
        if action == "wait":
            deadline = asyncio.get_running_loop().time() + 60 * (wait_minutes or 20)
            while (status := await inv.runs.status(run_id)) not in (*FINISHED, STUCK):
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
            lines = [_log_line(f) for f in files]
            if job["kind"] == "investigation":
                lines.append(await _import_investigations(files, job["address"]))
            record = inv.record()
            record.jobs = [
                {**j, "collected": True} if j["label"] == label else j for j in record.jobs
            ]
            return f"{label}: {status}; collected to /inputs/jobs/{label}/\n" + "\n".join(lines)
        raise ToolError("action must be list, status, wait, collect, log or stop")

    return execute


@tool
def start_register(task: str | None, policy: LaunchPolicy | None = None) -> Tool:
    async def execute(task_args: str, checks: list[str]) -> str:
        """Start the register: every item of the benchmark, against the checks you will run.

        Lays out the population: the benchmark's own items, numbered as an eval numbers
        them, each with an empty cell per check. A cell fills as investigation jobs are
        collected and as you review; an empty one is work not yet done. Start it once,
        before collecting investigations, with the task arguments your investigations will use.

        Args:
            task_args: The benchmark's arguments as a JSON object, as the investigations run it.
            checks: The investigation checks to track, e.g. ["gold-answer", "answer-format"].
        """
        from inspect_ai.util import registry_create

        from ._register import Register, load, save, started
        from ._task import numbered
        from ._verdicts import investigation_items

        if not task:
            raise ToolError(
                "the register needs the investigated task: the operator sets target_task"
            )
        if started(load()):
            raise ToolError("the register is already started; it keeps one population")
        args = json_object(task_args, "task_args")
        if policy is not None:
            ensure_policy(policy, store_as(PolicyStore), require_existing=True)
            if canonical(args) != policy.task_args_json or set(checks) - set(policy.checks):
                raise ToolError("register arguments and checks must match the launch policy")
        if policy is not None and set(checks) != set(policy.checks):
            raise ToolError("Start the register with all checks declared by the launch policy")
        try:
            skills = investigation_items(checks)
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
        if policy is not None and policy.samples is not None:
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
        return (
            f"register started: {len(dataset)} items x {len(skills)} checks, at "
            "/workspace/findings.json. register(view='summary') shows progress."
        )

    return execute


@tool(name="register")
def register_view() -> Tool:
    async def execute(view: str, check: str | None) -> str:
        """What the register holds, by state: the cells to work through next.

        Args:
            view: "summary" (counts per check), "flagged" (an investigator flagged a
                problem you have not reviewed), "unresolved" (the standing take is
                unresolved), "missing" (no take yet) or "disputed" (takes disagree).
            check: Only this check; null for all.
        """
        from ._register import load, select, started
        from ._verdicts import investigation_items

        current = load()
        if not started(current):
            raise ToolError("no register yet: start_register first")
        if view not in ("summary", "flagged", "unresolved", "missing", "disputed"):
            raise ToolError("view is summary, flagged, unresolved, missing or disputed")
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


async def _import_investigations(files: list[Path], address: str) -> str:
    """An investigation job's verdicts into the register, if one is started."""
    from inspect_ai.log import read_eval_log

    from ._register import import_investigation_log, load, save, started
    from ._verdicts import investigation_items

    current = load()
    if not started(current):
        return (
            "  (no register started: these verdicts are not in it; "
            "start_register, then collect again)"
        )
    skills = {s.name: s for s in investigation_items()}
    flags = {g for s in skills.values() for g in s.flags}
    added: list[str] = []
    try:
        for file in files:
            added += import_investigation_log(current, read_eval_log(str(file)), address, skills)
    except ValueError as ex:
        raise ToolError(f"cannot add this investigation to the register: {ex}") from ex
    await save(current)
    flagged = sum(1 for a in added if a.split("=")[-1] in flags)
    return f"  register: {len(added)} investigator take(s) added, {flagged} flagged"


def _log_line(path: Path) -> str:
    """One log, summarised from its samples: how many, how they ended, what they cost."""
    summaries = read_eval_log_sample_summaries(str(path))
    errors = sum(1 for s in summaries if s.error)
    cost = sum((u.total_cost or 0.0) for s in summaries for u in (s.model_usage or {}).values())
    return f"  {path.name}: {len(summaries)} samples, {errors} errored, ${cost:.2f} recorded"


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
    investigator_image: str | None = None,
    investigator_models: list[str] | None = None,
    subject_models: list[str] | None = None,
    judge_models: list[str] | None = None,
    auditor_image: str | None = None,
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
    model_prices: dict[str, dict[str, float]] | None = None,
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
        investigator_image: Published investigator image for investigation jobs on Kubernetes.
        investigator_models: Full Inspect model names an investigation job may use as its investigator.
        subject_models: Full Inspect model names a benchmark job may evaluate.
        judge_models: Full Inspect model names the benchmark's own judges may be. Declare the
            judges the investigated logs record; none by default.
        auditor_image: Published auditor sandbox image for Hawk.
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
        model_prices: Explicit per-million token prices for models without Inspect
            cost metadata. OpenRouter routes can use their published prices.
    """
    # the file is read before anything else is decided: an argument is "given" only
    # when it is not None, so a value equal to a default is still an override
    settings = _audit_file(config, locals()) if config else {}
    from ._findings import Findings, encoded, submit_findings
    from ._publishing import PhaseTools, load_publisher

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
    investigator_image = str(
        settings.get("investigator_image", investigator_image) or DEFAULT_INVESTIGATOR_IMAGE
    )
    menu = ModelMenu(
        investigators=list(
            settings.get("investigator_models", investigator_models) or DEFAULT_WORKERS
        ),
        subjects=list(settings.get("subject_models", subject_models) or DEFAULT_WORKERS),
        judges=list(settings.get("judge_models", judge_models) or []),
    )
    auditor_image = str(settings.get("auditor_image", auditor_image) or DEFAULT_AUDITOR_IMAGE)
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
    price_table: dict[str, dict[str, float]] = settings.get("model_prices", model_prices) or {}
    model_prices = price_table
    register_prices(price_table)
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
    credential_mode = settings.get("credential_mode", credential_mode) or "user"
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
    if credential_mode not in ("restricted", "user"):
        raise ValueError("credential_mode must be 'restricted' or 'user'")
    if credential_mode == "restricted" and runs != "hawk":
        raise ValueError("restricted credentials require runs='hawk'")
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
    if "hawk" in (runs, "k8s" if box == "k8s" else None) and not hawk_api_url:
        raise ValueError("Hawk work needs hawk_api_url (or HAWK_API_URL)")
    if not repo:
        raise ValueError(
            "audit needs a repo: the benchmark to investigation, or a config naming one"
        )
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
        if not task_package or not investigation_package:
            raise ValueError("declare task_package and investigation_package for child jobs")
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
        investigation_package=investigation_package,
        investigator_image=investigator_image,
        model_configs_json=canonical(model_configs),
        model_prices_json=canonical(model_prices),
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

    tools: list[Tool] = [
        # a shell dump is the one place the agent routinely wants more than the
        # 32KB default; everything else writes large results to files
        ToolDef(bash(timeout=300), max_output=128 * 1024).as_tool(),
        skill(skill_paths),
        # the agent's own calls bill to the Hawk user's key in a pod, to
        # OPENROUTER_API_KEY in a local box; jobs to whichever their backend uses
        budget(
            hawk_api_url if "hawk" in (runs, "k8s" if box == "k8s" else None) else None,
            openrouter=box == "docker" or runs == "local",
        ),
        view_image(),
        report_blocker(),
        start_register(target_task, launch_policy),
        register_view(),
    ]
    inv = None
    if runs:
        if task_package is None or investigation_package is None or target_task is None:
            raise RuntimeError("child-job packages and task were not resolved")
        inv = Audit(
            root=root,
            # local runs outside the per-construction folder: a resumed run must find them
            runs=HawkRuns(
                hawk_api_url or "",
                credential_mode=launch_policy.credential_mode,
                secrets={name: os.environ[name] for name in child_secrets},
            )
            if runs == "hawk"
            else LocalRuns(Path(output_dir).expanduser().resolve() / "runs"),
            where=runs,
            task=target_task,
            task_package=task_package,
            investigation_package=investigation_package,
            investigator_image=investigator_image,
            menu=menu,
            supplied=sorted(set(supplied)),
            authorizer=AuditAuthorizer(launch_policy),
        )
        tools += [run_benchmark(inv), run_investigation(inv), jobs(inv)]
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

    from ._audit_workspace import configure, stage_inputs

    sandbox = configure(root, auditor_image) if box == "k8s" else docker_box(root)
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

    from inspect_ai.solver import Generate, Solver, solver

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
        setup=[bind_policy(launch_policy), stage_inputs(root), begin_findings()],
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
        solver=react(
            name="auditor",
            prompt=prompts.AUDIT,
            submit=False,
            tools=[phase_tools],
            # an absolute threshold: 0.8 of a 1.05M-token context fired at ~840k,
            # long after every turn had become expensive
            compaction=CompactionSummary(threshold=COMPACTION_TOKENS),
            truncation="auto",
            on_continue=on_continue,
        ),
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
