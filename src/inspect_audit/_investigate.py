"""The investigator: a react agent in its own box, launching benchmark and audit jobs.

An investigation is one Inspect task. Its agent works in one box, with the inputs
the host staged there. Jobs it launches run through `_runs`, on Hawk or locally,
and bill to the same model key as the agent itself: that key's limit is the
investigation's budget, enforced by the provider. Inspect's checkpointing carries
the box and the store across a crash; the store holds the jobs launched.
"""

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
from dataclasses import dataclass
from importlib.metadata import version
from logging import getLogger
from pathlib import Path
from typing import Any
from uuid import uuid4

import yaml
from inspect_ai import Task, task
from inspect_ai.agent import AgentState, react
from inspect_ai.dataset import Sample
from inspect_ai.log import list_eval_logs, read_eval_log_sample_summaries
from inspect_ai.model import CompactionSummary, GenerateConfig
from inspect_ai.tool import Tool, ToolDef, ToolError, bash, skill, tool
from inspect_ai.util import CheckpointConfig, store_as

from . import prompts
from ._image import view_image
from ._prices import valid_price
from ._report import (
    InvestigationRecord,
    InvestigationState,
    check_report,
    investigation_outcome,
    operator_turn,
    report_blocker,
)
from ._report import (
    publish_report as publish_report,
)
from ._report import (
    save_publication as save_publication,
)
from ._report import (
    validate_findings as validate_findings,
)
from ._runs import FINISHED, HawkRuns, Job, LocalRuns, Runs
from ._verdicts import LOG_SKILLS, SKILLS, SUPPORT_SKILLS

logger = getLogger(__name__)

ASSETS = Path(__file__).parent / "investigation"

# Ours first, then the vendored ones, adapted for a container with no user in it and no
# `hawk` binary. See investigation/skills/VENDORED.md for provenance and what changed.
DEFAULT_AUDITOR_IMAGE = (
    "ghcr.io/generality-labs/inspect-audit-auditor@sha256:"
    "63665b4948b4adfe9767079ceb9a5d70fe39c0f56518e700a55b0313fa1e52aa"
)
DEFAULT_INVESTIGATOR_IMAGE = (
    "ghcr.io/generality-labs/inspect-audit-auditor@sha256:"
    "d21b285bc4ce2f2d34084c2ae382ab5a6996704dbb8d58d1199a1d1925fa71fa"
)

INVESTIGATION_SKILLS = (
    "investigating",
    "writing",
    "running-jobs",
    "eval-validity-review",
    "investigate-dataset",
    "security-audit-eval",
    "check-trajectories-workflow",
    "eval-report-workflow",
    "read-eval-logs",
)
# compact the investigator's context once it passes this many tokens
COMPACTION_TOKENS = 200_000
OPENROUTER = "https://openrouter.ai/api/v1"


@dataclass(frozen=True)
class ModelMenu:
    """The models an investigation may use, by the part each plays."""

    auditors: list[str]
    subjects: list[str]
    judges: list[str]

    @property
    def all(self) -> list[str]:
        return list(dict.fromkeys([*self.auditors, *self.subjects, *self.judges]))


DEFAULT_WORKERS = [
    "openai/gpt-5.6-luna",
    "google/gemini-3.6-flash",
    "openai/gpt-5-mini",
    "openai/gpt-5.6-sol",
    "openai/gpt-5.6-terra",
    "openai/gpt-6-astra",
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
        # only the named paths when given: the task under audit, not the whole
        # collection it ships in -- other evals are noise the agent should not read
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
        # unpacked here rather than in the box: the agent should find files, not a tar
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
    """Copy documentation trees read-only into inputs/docs/<name>/."""
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


def _investigation_file(config: str, passed: dict[str, Any]) -> dict[str, Any]:
    """Task settings from a YAML file, so an investigation is a document, not a command.

    Every key is a parameter of this task, and an argument given on the command line
    wins over the file, so a saved investigation can be re-run with one thing changed.
    """
    path = Path(config).expanduser()
    if not path.is_file():
        raise ValueError(f"no such investigation file: {config}")
    loaded = yaml.safe_load(path.read_text()) or {}
    if not isinstance(loaded, dict):
        raise ValueError(f"{config} must be a mapping of task settings")
    signature = inspect_module.signature(investigate)
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
        logger.info(f"auditing {only}: the only task {source} declares")
        return only
    return None


def is_remote_logs(source: str) -> bool:
    """Whether a log source is already parked somewhere a Hawk job can read it."""
    return source.startswith(("hawk:", "s3://", "gs://", "http://", "https://"))


def git_package_spec(repo: Path, revision: str | None = None) -> str | None:
    """The pip spec that installs this checkout's code somewhere else.

    A Hawk runner is a fresh pod: it installs the benchmark to run it. That spec is
    the same repository the agent reads, at the same commit, so deriving it here
    removes the chance of the agent auditing one commit while the jobs run another.
    """
    try:
        origin = subprocess.check_output(
            ["git", "-C", str(repo), "remote", "get-url", "origin"], text=True
        ).strip()
        commit = subprocess.check_output(
            ["git", "-C", str(repo), "rev-parse", "--verify", f"{revision or 'HEAD'}^{{commit}}"],
            text=True,
        ).strip()
    except (subprocess.CalledProcessError, OSError):
        return None
    if not origin.startswith(("https://", "git@", "ssh://")):
        return None
    url = origin.removesuffix(".git").replace("git@github.com:", "https://github.com/")
    on_remote = subprocess.run(
        ["git", "-C", str(repo), "branch", "-r", "--contains", commit],
        capture_output=True,
        text=True,
        check=False,
    )
    if on_remote.returncode != 0 or not on_remote.stdout.strip():
        logger.warning(
            f"{url}@{commit[:8]} is not on any remote branch: a Hawk runner will not be "
            "able to install it. Push the commit, or pass the package spec explicitly."
        )
    return f"git+{url}@{commit}"


def own_package_spec() -> str | None:
    """The spec that installs the inspect_audit a runner should use: this one."""
    from importlib.metadata import Distribution, PackageNotFoundError

    try:
        dist = Distribution.from_name("inspect_audit")
    except PackageNotFoundError:  # pragma: no cover - not installed
        return None
    direct_url = dist.read_text("direct_url.json")
    if direct_url:
        data = json.loads(direct_url)
        vcs = data.get("vcs_info") or {}
        if vcs.get("vcs") == "git" and vcs.get("commit_id"):
            return f"git+{data['url'].removesuffix('.git')}@{vcs['commit_id']}"
        # an editable or local install: the code is a working tree, so ask git
        local = data.get("url", "")
        if local.startswith("file://"):
            return git_package_spec(Path(local.removeprefix("file://")))
    return None


def paths_from_metadata(source: Path, target_task: str | None) -> list[str] | None:
    """The task's own directory, plus what the package shares with it.

    Auditing one eval out of a collection, the rest of the collection is noise: the
    agent reads a hundred other tasks' code looking for the one it was asked about.
    The eval's own metadata says which directory is its own; the shared modules next
    to it are included because the task imports them.
    """
    from ._source import collect_source

    directory = _task_directory(source, target_task)
    if directory is None:
        return None
    # what the task's code imports, followed the way the auditor's source copy is
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
    """Where an eval's own code lives.

    Two ways, because benchmarks are not all shaped like `inspect_evals`. Its evals
    publish an `eval.yaml` naming their tasks, and that is the most reliable answer
    where it exists. Everything else is found the way a person would: the file that
    declares the task with `@task`. A benchmark that hides its task behind a factory
    defeats both, and then the whole repository is snapshotted, which is the old
    behaviour rather than a wrong answer.
    """
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
    from ._registry import fetch_logs

    root = Path(output_dir).expanduser().resolve() / uuid4().hex
    inputs = root / "inputs"
    work = root / "work"
    inputs.mkdir(parents=True)
    work.mkdir()
    shutil.copyfile(Path(__file__).with_name("_coverage.py"), inputs / "coverage.py")
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
    shutil.copytree(
        ASSETS / "report",
        work / "report",
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )
    from ._assessment import prepare_assessments

    prepare_assessments(work / "report")
    (work / "journal.md").write_text(
        "# Activity journal\n\nAppend actions and corrections with evidence references.\n"
    )
    return root


def docker_box(root: Path) -> tuple[str, str]:
    """The investigator's box on local Docker: built here, nothing mounted."""
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


async def key_balance(hawk_api_url: str | None, openrouter: bool) -> list[str]:
    """What the model keys this investigation spends on have left, as their owners say.

    Calls made as a Hawk user (the investigator in a Hawk pod, jobs run on Hawk)
    bill to that user's key, which Hawk reports. Calls made on OPENROUTER_API_KEY
    (the investigator in a local box, local jobs) bill to that key, which OpenRouter
    reports. Which applies is declared by the caller; nothing is computed here.
    """

    def get(url: str, token: str) -> dict[str, Any]:
        request = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
        with urllib.request.urlopen(request, timeout=30) as response:
            return dict(json.load(response))

    lines: list[str] = []
    if hawk_api_url:
        from ._runs import HawkLogin

        try:
            login = HawkLogin(hawk_api_url)
            body = await asyncio.to_thread(
                get, f"{hawk_api_url.rstrip('/')}/usage/balance", await login.access_token()
            )
            if body.get("status") == "shared":
                lines.append("Hawk: this user has no capped key; spending is not limited by a key")
            else:
                lines.append(f"Hawk key: ${float(body.get('remaining_usd') or 0):.2f} remaining")
        except Exception as ex:
            lines.append(f"Hawk key: could not ask ({type(ex).__name__}: {ex})")
    if openrouter:
        try:
            key = os.environ["OPENROUTER_API_KEY"]
            data = (await asyncio.to_thread(get, f"{OPENROUTER}/key", key)).get("data") or {}
            if data.get("limit") is None:
                lines.append(f"OpenRouter key: no limit; ${float(data.get('usage') or 0):.2f} used")
            else:
                lines.append(
                    f"OpenRouter key: ${float(data.get('limit_remaining') or 0):.2f} of "
                    f"${float(data['limit']):.2f} remaining"
                )
        except Exception as ex:
            lines.append(f"OpenRouter key: could not ask ({type(ex).__name__}: {ex})")
    return lines or ["no model key declared to ask about"]


@tool(name="budget")
def budget(hawk_api_url: str | None, openrouter: bool) -> Tool:
    async def execute() -> str:
        """What the investigation's model key has left: this agent and every job draw on it.

        When it runs out, model calls fail with the provider's error, yours and your
        jobs' alike. Publish what you have before that.
        """
        return "\n".join(await key_balance(hawk_api_url, openrouter))

    return execute


def openrouter_prices(models: list[str]) -> dict[str, dict[str, float]]:
    """OpenRouter's published per-million prices, by the name Inspect runs each under.

    An audit job caps each item's spend with Inspect's cost limit, which needs a
    price for the auditor; the job registers these itself.
    """
    request = urllib.request.Request(
        f"{OPENROUTER}/models", headers={"User-Agent": "inspect_audit"}
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        listing = {str(m["id"]): m.get("pricing") or {} for m in json.load(response)["data"]}
    prices: dict[str, dict[str, float]] = {}
    for model in models:
        pricing = listing.get(model)
        if pricing is None:
            raise ValueError(f"OpenRouter does not list {model!r}")
        price = {
            key: float(pricing.get(field) or 0) * 1_000_000
            for key, field in (
                ("input", "prompt"),
                ("output", "completion"),
                ("input_cache_read", "input_cache_read"),
                ("input_cache_write", "input_cache_write"),
            )
        }
        if not valid_price(price):
            raise ValueError(f"OpenRouter lists no usable price for {model!r}")
        prices[f"openrouter/{model}"] = price
    return prices


@dataclass
class Investigation:
    """What the job tools need that the agent does not hold."""

    root: Path
    runs: Runs
    where: str  # "hawk" or "local"
    task: str  # the audited task's registry name
    task_package: str
    audit_package: str
    auditor_image: str
    menu: ModelMenu
    supplied: list[str]  # log sources the operator gave, as addresses a job can read

    def record(self) -> InvestigationRecord:
        return store_as(InvestigationRecord)

    def job(self, label: str) -> dict[str, Any]:
        found = [j for j in self.record().jobs if j["label"] == label]
        if not found:
            raise ToolError(f"no job {label!r}; jobs(action='list') shows them")
        return found[0]

    def allow(self, models: list[str], allowed: list[str], part: str) -> None:
        refused = [m for m in models if m not in allowed]
        if refused:
            raise ToolError(
                f"{', '.join(refused)} may not be {part}; allowed: {', '.join(allowed) or 'none'}"
            )

    async def launch(self, job: Job, kind: str, note: str) -> str:
        if any(j["label"] == job.label for j in self.record().jobs):
            raise ToolError(f"a job labelled {job.label!r} exists; choose another label")
        run_id = await self.runs.submit(job)
        address = (
            f"hawk:{run_id}" if self.where == "hawk" else str(Path(self.runs.root, run_id, "logs"))  # type: ignore[attr-defined]
        )
        # assigned back, not appended in place: the store records an assignment
        record = self.record()
        record.jobs = [
            *record.jobs,
            {"label": job.label, "kind": kind, "run_id": run_id, "address": address, "note": note},
        ]
        return (
            f"Launched {kind} job {job.label!r} ({address}). jobs(action='wait', "
            f"label='{job.label}') blocks until it finishes; jobs(action='collect', ...) "
            f"then brings its logs to /inputs/jobs/{job.label}/."
        )


# task argument names that choose where code runs or what it reads: a job's task
# arguments may not set them. A benchmark that takes `sandbox="local"` would run the
# evaluated model's commands on the machine running the job, outside any container.
# whole words of a snake_case name: `profile` is not a file, `data_dir` is a dir
PLACEMENT_ARG = re.compile(
    r"(^|_)(sandbox|image|docker|compose|path|filepath|filename|file|dir|directory|workdir|root|url|host)s?($|_)",
    re.IGNORECASE,
)
MODEL_ARG = re.compile(r"(^|_)(model|judge|grader)s?($|_)", re.IGNORECASE)


def argument_problems(args: Any, allowed: list[str], where: str = "task_args") -> list[str]:
    """What a job's task arguments may not contain, at any depth.

    No argument that places code or names a file, and no model outside the
    investigation's lists. A task builds its own models from names in its arguments,
    so they are held to the same lists as the job's models, in Inspect's spelling.
    """
    problems: list[str] = []
    if isinstance(args, dict):
        for key, value in args.items():
            here = f"{where}.{key}"
            if PLACEMENT_ARG.search(str(key)):
                problems.append(f"{here}: jobs may not set where code runs or what it reads")
            elif MODEL_ARG.search(str(key)) and isinstance(value, str):
                if value.removeprefix("openrouter/") not in allowed or "/" not in value:
                    problems.append(
                        f"{here}: {value!r} is not an allowed model; write one of "
                        + ", ".join(f"openrouter/{m}" for m in allowed)
                    )
            else:
                problems += argument_problems(value, allowed, here)
    elif isinstance(args, list):
        for index, value in enumerate(args):
            problems += argument_problems(value, allowed, f"{where}[{index}]")
    return problems


def check_arguments(task_args: dict[str, Any], allowed: list[str]) -> None:
    if problems := argument_problems(task_args, allowed):
        raise ToolError("task_args refused:\n- " + "\n- ".join(problems))


@tool
def run_benchmark(inv: Investigation) -> Tool:
    async def execute(
        label: str,
        models: list[str],
        sample_ids: list[str] | None,
        epochs: int,
        task_args: dict[str, Any],
        model_roles: dict[str, str],
        note: str,
    ) -> str:
        """Run the audited benchmark: fresh attempts by the models you name.

        Args:
            label: A short name for this job, unique in the investigation.
            models: Models to evaluate, as OpenRouter ids from the subject list.
            sample_ids: The benchmark's sample ids to run; null runs every sample.
            epochs: Attempts per sample per model.
            task_args: The task's arguments, exactly as the benchmark takes them;
                {} for its defaults. A model named in one must be a judge, written
                openrouter/<id>; arguments that set a sandbox, image, path or file
                are refused.
            model_roles: Role name to model for the benchmark's own judges, from the
                judge list; {} when it has none.
            note: Why you are running this.
        """
        inv.allow(models, inv.menu.subjects, "evaluated")
        inv.allow(list(model_roles.values()), inv.menu.judges, "judges")
        check_arguments(task_args, inv.menu.judges)
        job = Job(
            label=label,
            task=inv.task,
            package=inv.task_package,
            models=models,
            task_args=task_args,
            model_roles=model_roles,
            sample_ids=sample_ids,
            epochs=epochs,
        )
        return await inv.launch(job, "benchmark", note)

    return execute


@tool
def run_audit(inv: Investigation) -> Tool:
    async def execute(
        label: str,
        samples: list[str],
        checks: list[str],
        logs: list[str],
        auditor_model: str,
        item_cost_limit: float,
        task_args: dict[str, Any],
        judge: dict[str, str],
        note: str,
    ) -> str:
        """Audit benchmark items with inspect_audit's auditor, one sample per item.

        Args:
            label: A short name for this job, unique in the investigation.
            samples: The benchmark's sample ids to audit.
            checks: Checks to run, e.g. ["gold-answer", "red-teaming"].
            logs: Recorded attempts to audit against: labels of benchmark jobs you
                ran, or supplied log sources from the seed; [] for none.
            auditor_model: The auditor, an OpenRouter id from the auditor list.
            item_cost_limit: Dollars one audited item may spend.
            task_args: The benchmark's arguments, exactly as the logs ran it, under
                the same rules as run_benchmark's.
            judge: Role name to model for the benchmark's judge, from the judge list.
                With logs, it must be the model the logs record; {} for none.
            note: Why you are running this.
        """
        inv.allow([auditor_model], inv.menu.auditors, "the auditor")
        inv.allow(list(judge.values()), inv.menu.judges, "judges")
        check_arguments(task_args, inv.menu.judges)
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
            prices = openrouter_prices([auditor_model, *judge.values()])
        except Exception as ex:
            raise ToolError(f"cannot price the auditor for its cost limit: {ex}") from ex
        job = Job(
            label=label,
            task="inspect_audit/audit",
            package=inv.audit_package,
            extra_packages=[inv.task_package],
            models=[auditor_model],
            task_args={
                "task": inv.task,
                "task_args": task_args,
                "samples": samples,
                "checks": checks,
                "logs": addresses or None,
                "judge": {r: f"openrouter/{m}" for r, m in judge.items()} or None,
                "auditor_image": inv.auditor_image,
                "cost_limit": item_cost_limit,
                "model_prices": prices,
            },
        )
        return await inv.launch(job, "audit", note)

    return execute


@tool
def jobs(inv: Investigation) -> Tool:
    async def execute(action: str, label: str | None, wait_minutes: float | None) -> str:
        """See and manage the jobs this investigation launched.

        Args:
            action: "list" every job; "status" of one; "wait" until one finishes
                (spends no tokens); "collect" a finished job's logs into
                /inputs/jobs/<label>/; "log" the tail of its runner's own log, where
                install failures and crashes show; "stop" it.
            label: Which job, for every action but "list".
            wait_minutes: How long "wait" may block; null means twenty.
        """
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
            while (status := await inv.runs.status(run_id)) not in FINISHED:
                if asyncio.get_running_loop().time() > deadline:
                    break
                await asyncio.sleep(30)
            return f"{label}: {status}"
        if action == "log":
            return await inv.runs.runner_log(run_id)
        if action == "stop":
            await inv.runs.stop(run_id)
            return f"stop requested for {label}"
        if action == "collect":
            from ._investigation_workspace import copy_into_box

            status = await inv.runs.status(run_id)
            if status not in FINISHED:
                raise ToolError(f"{label} is {status}; wait for it before collecting")
            host = inv.root / "inputs" / "jobs" / label
            files = await inv.runs.logs(run_id, host)
            if not files:
                raise ToolError(f"{label} ({status}) wrote no logs; jobs(action='log') shows why")
            if files[0].parent != host:  # a local run's logs are already on this machine
                host.mkdir(parents=True, exist_ok=True)
                files = [Path(shutil.copy2(f, host / f.name)) for f in files]
            await copy_into_box(files, f"/inputs/jobs/{label}")
            record = inv.record()
            record.jobs = [
                {**j, "collected": True} if j["label"] == label else j for j in record.jobs
            ]
            return f"{label}: {status}; collected to /inputs/jobs/{label}/\n" + "\n".join(
                _log_line(f) for f in files
            )
        raise ToolError("action must be list, status, wait, collect, log or stop")

    return execute


def _log_line(path: Path) -> str:
    """One log, summarised from its samples: how many, how they ended, what they cost."""
    summaries = read_eval_log_sample_summaries(str(path))
    errors = sum(1 for s in summaries if s.error)
    cost = sum((u.total_cost or 0.0) for s in summaries for u in (s.model_usage or {}).values())
    return f"  {path.name}: {len(summaries)} samples, {errors} errored, ${cost:.2f} recorded"


async def _continue(state: AgentState, interactive: bool) -> bool | str:
    if store_as(InvestigationState).outcome == "blocked":
        return False
    if not store_as(InvestigationState).published:
        return (
            True
            if state.output.message.tool_calls
            else "Continue the investigation, or write and publish a report explaining the evidence and any limitations."
        )
    if not interactive:
        return False
    return await operator_turn(state)


@task
def investigate(
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
    audit_package: str | None = None,
    task_package: str | None = None,
    auditor_image: str | None = None,
    auditor_models: list[str] | None = None,
    subject_models: list[str] | None = None,
    judge_models: list[str] | None = None,
    investigator_image: str | None = None,
    artifact_dir: str | None = None,
    instructions: str | None = None,
    required_coverage: float | None = None,
) -> Task:
    """Investigate a benchmark's source and logs, and publish a GL LaTeX report.

    Runs wherever Inspect runs it. The agent's box (`box`) and where its jobs run
    (`runs`) are declared, never inferred. Every model call bills to the key it is
    made on, which is the budget: a capped OpenRouter key locally, the Hawk user's
    assigned key on Hawk.

    Args:
        repo: Local Git repository or HTTPS Git URL of the benchmark.
        config: YAML file setting any of these arguments, so an investigation can be a
            document rather than a command line. Anything also passed with -T wins.
        logs: Inspect log files or directories, or hawk:<eval set id> sources.
        paper: Local file or URL; a URL is downloaded now (arXiv abs -> pdf). Defaults to
            the paper the eval names in its own metadata.
        docs: Documentation directories to copy in read-only (inspect docs, Hawk docs).
        overview: Benchmark-specific context, supplied-evidence context and operator
            questions.
        target_task: The task under audit, e.g. `benchmark/task`. Defaults to the only
            task the repository declares, when there is one.
        revision: Commit to snapshot (default HEAD).
        paths: Repository paths to include in the snapshot. Defaults to the audited
            task's own directory and the modules its package shares.
        output_dir: Where the host keeps the investigation's files, including the
            published report. Defaults to ~/.inspect-audit/investigations.
        runs: Where the agent's jobs run: "hawk" or "local". None gives it no jobs.
        box: The agent's own box: "docker" (default), or "k8s" for a pod on Hawk.
        hawk_api_url: Hawk's API, for jobs and balances on Hawk. Defaults to
            HAWK_API_URL, Hawk's own setting.
        token_limit: Optional Inspect token limit (e.g. "output:500k").
        interactive: Wait for the operator over ACP after publishing.
        extra_skills: Additional skill directories to load.
        audit_package: pip spec of inspect_audit for audit jobs. Defaults to the
            commit this process is running.
        task_package: pip spec of the benchmark for jobs. Defaults to the source
            repository and revision.
        auditor_image: Published auditor image for audit jobs on Kubernetes.
        auditor_models: OpenRouter ids an audit job may use as its auditor.
        subject_models: OpenRouter ids a benchmark job may evaluate.
        judge_models: OpenRouter ids the benchmark's own judges may be. Declare the
            judges the audited logs record; none by default.
        investigator_image: Published investigator image, including LaTeX, for Hawk.
        artifact_dir: Where the published report is also saved, under this sample:
            on Hawk, the job's S3 artifacts directory. None keeps it on the host only.
        instructions: Operator instructions and scope, separate from benchmark background.
        required_coverage: Fraction of the question population (0-1) the published
            coverage.json must assess for the run to count as complete.
    """
    # the file is read before anything else is decided: an argument is "given" only
    # when it is not None, so a value equal to a default is still an override
    settings = _investigation_file(config, locals()) if config else {}
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
    audit_package = settings.get("audit_package", audit_package)
    task_package = settings.get("task_package", task_package)
    auditor_image = str(settings.get("auditor_image", auditor_image) or DEFAULT_AUDITOR_IMAGE)
    menu = ModelMenu(
        auditors=list(settings.get("auditor_models", auditor_models) or DEFAULT_WORKERS),
        subjects=list(settings.get("subject_models", subject_models) or DEFAULT_WORKERS),
        judges=list(settings.get("judge_models", judge_models) or []),
    )
    investigator_image = str(
        settings.get("investigator_image", investigator_image) or DEFAULT_INVESTIGATOR_IMAGE
    )
    artifact_dir = settings.get("artifact_dir", artifact_dir)
    instructions = settings.get("instructions", instructions)
    required_coverage = settings.get("required_coverage", required_coverage)

    if runs not in (None, "hawk", "local"):
        raise ValueError("runs must be 'hawk', 'local' or None")
    if box not in ("docker", "k8s"):
        raise ValueError("box must be 'docker' or 'k8s'")
    if "hawk" in (runs, "k8s" if box == "k8s" else None) and not hawk_api_url:
        raise ValueError("Hawk work needs hawk_api_url (or HAWK_API_URL)")
    if required_coverage is not None and not (0 < float(required_coverage) <= 1):
        raise ValueError("required_coverage must be a fraction in (0, 1]")
    if not repo:
        raise ValueError("investigate needs a repo: the benchmark to audit, or a config naming one")
    skill_paths = [str(ASSETS / "skills" / name) for name in INVESTIGATION_SKILLS] + [
        str(SKILLS / name) for name in (*LOG_SKILLS, *SUPPORT_SKILLS)
    ]
    for path in extra_skills or []:
        resolved = Path(path).expanduser().resolve()
        if not (resolved / "SKILL.md").is_file():
            raise ValueError(f"Expected a skill directory containing SKILL.md: {path}")
        skill_paths.append(str(resolved))

    # what can be worked out is worked out: the audited repository already says which
    # commit it is, which directory the task lives in and which paper it comes from
    local_repo = Path(repo).expanduser()
    if local_repo.is_dir():
        target_task = target_task or _only_task(local_repo)
        paths = paths or paths_from_metadata(local_repo, target_task)
        paper = paper or paper_from_metadata(local_repo, target_task)
    # somewhere every machine running this can write, and a person can find
    output_dir = output_dir or str(Path.home() / ".inspect-audit" / "investigations")
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
        check_report(str(root)),
        publish_report(str(root), required_coverage),
        report_blocker(),
    ]
    seed_path = root / "inputs" / "seed.json"
    seed = json.loads(seed_path.read_text())
    if required_coverage is not None:
        seed["required_coverage"] = float(required_coverage)
    if runs:
        if not target_task:
            raise ValueError("jobs run the audited task: name it with target_task")
        # a job installs the benchmark from git: a local checkout supplies its own
        # origin and commit, and a repository given as a URL is already that answer
        task_package = task_package or (
            git_package_spec(local_repo, revision)
            if local_repo.is_dir()
            else f"git+{repo.removesuffix('.git')}@{revision}"
            if revision
            else None
        )
        audit_package = audit_package or own_package_spec()
        if not task_package or not audit_package:
            raise ValueError(
                "jobs install the benchmark and inspect_audit from pip specs, and this "
                "process cannot say what they are: pass task_package and audit_package"
            )
        supplied = [str(e["remote"]) for e in seed["logs"] if e.get("remote")]
        if runs == "local":
            supplied += [str(e["source"]) for e in seed["logs"] if not e.get("remote")]
        inv = Investigation(
            root=root,
            runs=HawkRuns(hawk_api_url or "") if runs == "hawk" else LocalRuns(root / "runs"),
            where=runs,
            task=target_task,
            task_package=task_package,
            audit_package=audit_package,
            auditor_image=auditor_image,
            menu=menu,
            supplied=sorted(set(supplied)),
        )
        tools += [run_benchmark(inv), run_audit(inv), jobs(inv)]
        seed["jobs"] = {
            "runs": runs,
            "task": target_task,
            "auditor_models": menu.auditors,
            "subject_models": menu.subjects,
            "judge_models": menu.judges,
            "supplied_logs": inv.supplied,
        }
    seed_path.write_text(json.dumps(seed, indent=2))

    from ._investigation_workspace import configure, stage_inputs

    sandbox = configure(root, investigator_image) if box == "k8s" else docker_box(root)
    if artifact_dir:
        (root / "artifacts.json").write_text(json.dumps({"artifact_dir": artifact_dir}))
    elif box == "k8s":
        logger.warning(
            "no artifact_dir: the published report stays on this host only, and on a "
            "Hawk runner it is lost when the pod ends"
        )

    async def on_continue(state: AgentState) -> bool | str:
        return await _continue(state, interactive)

    return Task(
        setup=[stage_inputs(root)],
        dataset=[
            Sample(
                id="investigation",
                input="Read /inputs/seed.json and invoke the investigating skill. Begin the investigation autonomously."
                + (f"\n\nOperator instructions:\n{instructions}" if instructions else ""),
            )
        ],
        scorer=investigation_outcome(),
        solver=react(
            name="investigator",
            prompt=prompts.INVESTIGATE,
            submit=False,
            tools=tools,
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
            "investigation_dir": str(root),
            "interactive": interactive,
            "runs": runs,
            "box": box,
        },
    )
