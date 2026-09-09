"""Local, artifact-first investigation using Inspect's standard agent and ACP."""

import asyncio
import errno
import json
import math
import os
import re
import shutil
import subprocess
import urllib.request
from html.parser import HTMLParser
from importlib.metadata import version
from logging import getLogger
from pathlib import Path
from typing import Any
from uuid import uuid4

import yaml
from inspect_ai import Task, task
from inspect_ai.agent import AgentState, react
from inspect_ai.dataset import Sample
from inspect_ai.log import list_eval_logs
from inspect_ai.model import (
    CompactionSummary,
    GenerateConfig,
    ModelCost,
    ModelInfo,
    set_model_info,
)
from inspect_ai.model._model import sample_model_usage
from inspect_ai.tool import Tool, ToolError, bash, skill, tool
from inspect_ai.util import sandbox, store_as

from ._agent import SKILLS, SUPPORT_SKILLS, view_image
from ._jobs import (
    Hawk,
    Job,
    JobLedger,
    audit_config,
    benchmark_config,
    copy_into_inputs,
    slug,
    stage_logs_to_s3,
    usage_cost,
    utcnow,
    wait_for,
    write_config,
)
from ._report import (
    InvestigationState,
    _operator_turn,
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

logger = getLogger(__name__)

ASSETS = Path(__file__).parent / "investigation"
PROMPT = (ASSETS / "prompt.md").read_text()
OPENROUTER_MODELS = "https://openrouter.ai/api/v1/models"
DEFAULT_WORKERS = [
    "openai/gpt-5.6-luna",
    "google/gemini-3.6-flash",
    "openai/gpt-5-mini",
    "openai/gpt-5.6-sol",
    "openai/gpt-5.6-terra",
    "anthropic/claude-sonnet-5",
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
        subprocess.run(
            [
                "git",
                "-C",
                str(source),
                "archive",
                "--format=tar",
                f"--output={inputs / 'source.tar'}",
                commit,
                *(["--", *paths] if paths else []),
            ],
            check=True,
        )
        return {
            "repo": str(source.resolve()),
            "revision": commit,
            "paths": paths or ["."],
            "source": "committed snapshot; excludes working-tree changes and submodule contents",
        }
    if not repo.startswith("https://"):
        raise ValueError("repo must be a local Git repository or an HTTPS Git URL")
    return {
        "repo": repo,
        "revision": revision or "HEAD",
        "paths": paths or ["."],
        "source": "clone at setup; resolved commit recorded in workspace",
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


def register_openrouter_costs(timeout: float = 15) -> int:
    """Register OpenRouter's current prices with Inspect, so a cost limit can bind.

    Inspect refuses a cost-limited run for a model without a price. OpenRouter
    publishes per-token prices for everything it serves; register them all as
    `openrouter/<id>` at prep time. Best effort: no network, no prices, and the
    cost limit then fails loudly at start rather than silently not applying.
    """
    try:
        request = urllib.request.Request(
            OPENROUTER_MODELS, headers={"User-Agent": "inspect_audit"}
        )
        with urllib.request.urlopen(request, timeout=timeout) as response:
            models = json.load(response)["data"]
    except Exception as ex:
        logger.warning(f"could not fetch OpenRouter prices: {ex}")
        return 0
    registered = 0
    for model in models:
        pricing = model.get("pricing") or {}
        try:
            per_million = {
                key: float(pricing.get(field) or 0) * 1_000_000
                for key, field in (
                    ("input", "prompt"),
                    ("output", "completion"),
                    ("input_cache_read", "input_cache_read"),
                    ("input_cache_write", "input_cache_write"),
                )
            }
        except (TypeError, ValueError):
            continue
        try:
            # set_model_info creates the entry; set_model_cost needs one to exist
            set_model_info(
                f"openrouter/{model['id']}", ModelInfo(cost=ModelCost(**per_million))
            )
        except Exception as ex:  # one odd listing must not lose the rest
            logger.debug(f"skipping price for {model.get('id')}: {ex}")
            continue
        registered += 1
    return registered


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
    budget_usd: float,
) -> Path:
    """Create a fresh run directory; expose only explicit inputs to the shell."""
    root = Path(output_dir).expanduser().resolve() / uuid4().hex
    inputs = root / "inputs"
    work = root / "work"
    inputs.mkdir(parents=True)
    work.mkdir()
    seed: dict[str, object] = dict(_snapshot_repo(repo, revision, inputs, paths))
    log_index = []
    for index, source in enumerate(logs):
        path = Path(source).expanduser().resolve()
        if not path.exists():
            raise ValueError(f"Local log source does not exist: {source}")
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
            relative = Path(str(index)) / (
                file.name if path.is_file() else file.relative_to(path)
            )
            _link_or_copy(file, inputs / "logs" / relative)
            log_index.append(
                {"source": str(file), "staged": f"/inputs/logs/{relative}"}
            )
    staged_paper = _fetch_paper(paper, inputs) if paper else None
    staged_docs = _stage_docs(docs, inputs)
    seed.update(
        task=target_task,
        overview=overview,
        paper=staged_paper,
        docs=staged_docs,
        logs=log_index,
        budget_usd=budget_usd,
    )
    (inputs / "seed.json").write_text(json.dumps(seed, indent=2))
    shutil.copytree(
        ASSETS / "report",
        work / "report",
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )
    (work / "journal.md").write_text(
        "# Activity journal\n\nAppend actions and corrections with evidence references.\n"
    )
    (root / "Dockerfile").write_text(
        (ASSETS / "Dockerfile")
        .read_text()
        .replace("INSPECT_VERSION", version("inspect-ai"))
    )
    (root / ".dockerignore").write_text("*\n!Dockerfile\n")
    compose = {
        "services": {
            "default": {
                "build": {"context": "."},
                "command": "sleep infinity",
                "init": True,
                "working_dir": "/workspace",
                "volumes": [
                    {
                        "type": "bind",
                        "source": str(inputs),
                        "target": "/inputs",
                        "read_only": True,
                    },
                    {"type": "bind", "source": str(work), "target": "/workspace"},
                ],
            }
        }
    }
    (root / "compose.yaml").write_text(yaml.safe_dump(compose))
    return root


class _Text(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []
        self.images: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in ("script", "style"):
            self._skip += 1
        if tag == "img":
            src = dict(attrs).get("src") or ""
            self.images.append(src[:60] + ("…" if len(src) > 60 else ""))
        if tag in ("p", "h1", "h2", "h3", "h4", "li", "tr", "pre", "div"):
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in ("script", "style"):
            self._skip = max(0, self._skip - 1)

    def handle_data(self, data: str) -> None:
        if not self._skip:
            self.parts.append(data)


@tool
def render_report() -> Tool:
    """Render the draft and read it back, without publishing."""

    async def execute() -> str:
        """Render report/report.qmd to HTML and return its text and any warnings.

        Use this to check the draft renders and reads the way you intend before
        calling publish_report. Figures are listed by source; look at a figure
        with view_image. Nothing is saved outside the workspace.
        """
        result = await sandbox().exec(
            ["quarto", "render", "/workspace/report/report.qmd", "--to", "html"],
            timeout=300,
        )
        if not result.success:
            raise ToolError(f"Rendering failed:\n{result.stderr}\n{result.stdout}")
        html = await sandbox().read_file("/workspace/report/report.html")
        parser = _Text()
        parser.feed(html)
        text = re.sub(r"\n{3,}", "\n\n", "".join(parser.parts)).strip()
        warnings = "\n".join(
            line for line in (result.stderr or "").splitlines() if "WARN" in line
        )
        return (
            f"Rendered ({len(text)} characters of text, {len(parser.images)} figures).\n"
            + (f"Quarto warnings:\n{warnings}\n" if warnings else "")
            + f"Figures: {parser.images}\n\n{text[:6000]}"
            + ("\n…" if len(text) > 6000 else "")
        )

    return execute


class Remote:
    """Everything the dispatch tools need that the agent must not hold."""

    def __init__(
        self,
        root: Path,
        hawk_api_url: str,
        secrets_file: str | None,
        task_package: str,
        audit_package: str,
        auditor_image: str,
        worker_models: list[str],
        log_bucket: str,
        aws_profile: str | None,
        allowance_usd: float,
    ) -> None:
        self.root = root
        self.hawk = Hawk(hawk_api_url, secrets_file)
        self.hawk_api_url = hawk_api_url
        self.task_package = task_package
        self.audit_package = audit_package
        self.auditor_image = auditor_image
        self.worker_models = worker_models
        self.log_bucket = log_bucket
        self.aws_profile = aws_profile
        self.allowance_usd = allowance_usd
        self.ledger = JobLedger(root)

    def check_model(self, model: str) -> None:
        if model not in self.worker_models:
            raise ToolError(
                f"{model!r} is not an allowed worker model. Allowed: {', '.join(self.worker_models)}"
            )

    def reserve(self, estimated_usd: float) -> None:
        if not (estimated_usd > 0):
            raise ToolError("estimated_usd must be a positive number: say what you expect this to cost")
        local = _local_spend()[0] or 0.0
        committed = local + self.ledger.actual_usd() + self.ledger.reserved_usd()
        if committed + estimated_usd > self.allowance_usd:
            raise ToolError(
                f"cannot reserve ${estimated_usd:.2f}: ${committed:.2f} of the ${self.allowance_usd:.2f} "
                "allowance is already spent or reserved. Collect finished jobs to release their "
                "reservations, or scale the request down."
            )


def _local_spend() -> tuple[float | None, list[str]]:
    usage = sample_model_usage()
    unpriced = [name for name, value in usage.items() if value.total_cost is None]
    known = sum(value.total_cost for value in usage.values() if value.total_cost is not None)
    return (None if unpriced else known), unpriced


@tool(name="budget")
def investigation_budget(
    budget_usd: float, enforce_cost_limit: bool, remote: Remote | None = None
) -> Tool:
    """Expose Inspect's accounting without inventing a separate pricing system."""

    async def execute() -> str:
        """Show the allowance, spend so far by model, and tokens used."""
        usage = sample_model_usage()
        lines = [f"Allowance: ${budget_usd:.2f}" + (" (enforced)" if enforce_cost_limit else " (planning only)")]
        total = 0.0
        unpriced: list[str] = []
        for name, value in usage.items():
            cost = value.total_cost
            if cost is None:
                unpriced.append(name)
            else:
                total += cost
            lines.append(
                f"  {name}: in {value.input_tokens or 0:,} | cache read "
                f"{value.input_tokens_cache_read or 0:,} | out {value.output_tokens or 0:,}"
                f" (reasoning {value.reasoning_tokens or 0:,}) | "
                + (f"${cost:.2f}" if cost is not None else "cost unknown")
            )
        if unpriced:
            lines.append(
                f"Spent: at least ${total:.2f}; {', '.join(unpriced)} unpriced, so the "
                "true total and the remaining allowance are unknown"
            )
        else:
            lines.append(f"Spent: ${total:.2f}   Remaining: ${max(0.0, budget_usd - total):.2f}")
        if remote is not None:
            jobs = remote.ledger.jobs
            lines.append(
                f"Remote jobs: {len(jobs)} submitted, ${remote.ledger.actual_usd():.2f} collected cost, "
                f"${remote.ledger.reserved_usd():.2f} reserved on jobs not yet collected"
            )
            for j in jobs:
                cost_text = (
                    f"${j.actual_usd:.2f}" if j.actual_usd is not None else f"reserved ${j.estimated_usd:.2f}"
                )
                lines.append(f"  {j.label} ({j.kind}, {j.eval_set_id}): {j.status}, {cost_text}")
            committed = (total if not unpriced else 0.0) + remote.ledger.actual_usd() + remote.ledger.reserved_usd()
            lines.append(f"Committed in total: ${committed:.2f} of ${budget_usd:.2f}")
        lines.append(
            "Scope: this investigator's own model calls plus remote jobs it launched. The allowance is shared across both."
        )
        return "\n".join(lines)

    return execute


@tool
def run_benchmark(remote: Remote, target_task: str | None, root: Path) -> Tool:
    """Run the benchmark under audit on Hawk."""

    async def execute(
        label: str,
        models: list[str],
        estimated_usd: float,
        limit: int | None = None,
        sample_ids: list[str] | None = None,
        epochs: int = 1,
        task: str | None = None,
        task_args: dict[str, Any] | None = None,
        reasoning_effort: str | None = None,
        note: str = "",
    ) -> str:
        """Submit an eval-set that runs the benchmark task itself with the given models.

        The job runs remotely on Hawk; this returns as soon as it is submitted.
        Use jobs(action="wait") to block until it finishes and jobs(action="collect")
        to bring its .eval logs under /inputs/jobs/<label>/. Start with one or two
        samples to prove the configuration before spending on a full run.

        Args:
            label: Short unique name for this job (letters, digits, hyphens).
            models: Worker models to evaluate, from the allowed list, e.g.
                "openai/gpt-5.6-luna".
            estimated_usd: What you expect this job to cost; it is reserved against the
                allowance until the job is collected and the real cost is known.
            limit: Evaluate only the first N samples.
            sample_ids: Evaluate exactly these sample ids instead.
            epochs: Repeats per sample.
            task: Registry name of the task (default: the task under audit).
            task_args: Task arguments (e.g. a prompt variant the task exposes).
            reasoning_effort: Reasoning effort for the worker models, if they take one.
            note: Why you are running this; recorded in the ledger.
        """
        task_name = task or target_task
        if not task_name:
            raise ToolError("no task given and the seed names no target task")
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]{1,30}", label):
            raise ToolError("label must be 2-31 lowercase letters, digits or hyphens")
        if remote.ledger.get(label):
            job = remote.ledger.get(label)
            return f"a job labelled {label!r} already exists ({job.eval_set_id}, {job.status}); use jobs() on it"  # type: ignore[union-attr]
        for m in models:
            remote.check_model(m)
        remote.reserve(estimated_usd)
        config = benchmark_config(
            name=f"inv-{slug(label)}",
            task_package=remote.task_package,
            task_name=task_name,
            models=[{"model": m, "reasoning_effort": reasoning_effort} for m in models],
            task_args=task_args,
            limit=limit,
            sample_ids=sample_ids,
            epochs=epochs,
            hawk_api_url=remote.hawk_api_url,
        )
        path = write_config(root, label, config)
        try:
            eval_set_id = remote.hawk.submit(path)
        except Exception as ex:
            raise ToolError(f"submission failed: {ex}") from ex
        remote.ledger.add(
            Job(label=label, kind="benchmark", eval_set_id=eval_set_id, config_path=str(path),
                submitted_at=utcnow(), estimated_usd=estimated_usd, note=note)
        )
        return (
            f"Submitted benchmark job {label!r} as Hawk eval set {eval_set_id} "
            f"({len(models)} model(s), limit={limit}, samples={sample_ids}, epochs={epochs}). "
            f"Config saved at jobs/{label}.eval-set.yaml. Reserved ${estimated_usd:.2f}."
        )

    return execute


@tool
def run_audit(remote: Remote, target_task: str | None, root: Path) -> Tool:
    """Run inspect_audit's sample auditors on Hawk over recorded attempts."""

    async def execute(
        label: str,
        logs: str,
        items: list[str],
        auditor_model: str,
        estimated_usd: float,
        grader_model: str | None = None,
        grader_reasoning_effort: str | None = None,
        auditor_reasoning_effort: str | None = None,
        limit: int | None = None,
        sample_ids: list[str] | None = None,
        notes: str | None = None,
        task: str | None = None,
        note: str = "",
    ) -> str:
        """Submit an eval-set that audits benchmark items, one auditor per item.

        Each auditor gets the item, its gold, the grader's source, the recorded
        attempts sliced from the logs, and the tools to grade attempts with the
        benchmark's own scorer. Its verdicts come back as scores in the job's .eval log.

        Args:
            label: Short unique name for this job.
            logs: Where the recorded attempts come from: "inputs" for the logs supplied
                to this investigation, the label of a benchmark job you ran, or
                "hawk:<eval-set-id>" for any Hawk eval set.
            items: Audit items to run, e.g. ["gold-answer", "answer-format",
                "red-teaming", "insufficiently-specified", "other-findings",
                "failure-attribution", "approach-census", "contamination",
                "ground-truth-access", "environment-integrity"].
            auditor_model: The model that audits, from the allowed list.
            estimated_usd: Expected cost, reserved until collected.
            grader_model: Model bound to the benchmark scorer's `grader` role when its
                scorer calls a model; read which grader the logs used and choose deliberately.
            grader_reasoning_effort: Reasoning effort for the grader model.
            auditor_reasoning_effort: Reasoning effort for the auditor model.
            limit: Audit only the first N items.
            sample_ids: Audit exactly these item ids.
            notes: Steer inserted into every auditor's system prompt (general, not answers).
            task: Registry name of the audited task (default: the task under audit).
            note: Why you are running this; recorded in the ledger.
        """
        task_name = task or target_task
        if not task_name:
            raise ToolError("no task given and the seed names no target task")
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]{1,30}", label):
            raise ToolError("label must be 2-31 lowercase letters, digits or hyphens")
        if remote.ledger.get(label):
            job = remote.ledger.get(label)
            return f"a job labelled {label!r} already exists ({job.eval_set_id}, {job.status}); use jobs() on it"  # type: ignore[union-attr]
        remote.check_model(auditor_model)
        if grader_model:
            remote.check_model(grader_model)
        remote.reserve(estimated_usd)
        eval_set_id: str | None = None
        if logs == "inputs":
            local = root / "inputs" / "logs"
            if not local.is_dir():
                raise ToolError("no logs were supplied to this investigation")
            eval_set_id = f"inv-{slug(label)}-{uuid4().hex[:6]}"
            try:
                logs_source = stage_logs_to_s3(local, remote.log_bucket, eval_set_id, remote.aws_profile)
            except Exception as ex:
                raise ToolError(str(ex)) from ex
        elif logs.startswith("hawk:"):
            logs_source = logs
        else:
            source_job = remote.ledger.get(logs)
            if source_job is None:
                raise ToolError(f"{logs!r} is neither 'inputs', a job label, nor 'hawk:<id>'")
            logs_source = f"hawk:{source_job.eval_set_id}"
        config = audit_config(
            name=f"inv-{slug(label)}",
            eval_set_id=eval_set_id,
            audit_package=remote.audit_package,
            task_package=remote.task_package,
            audited_task=task_name,
            logs_source=logs_source,
            items=items,
            limit=limit,
            sample_ids=sample_ids,
            auditor={"model": auditor_model, "reasoning_effort": auditor_reasoning_effort},
            grader={"model": grader_model, "reasoning_effort": grader_reasoning_effort} if grader_model else None,
            auditor_image=remote.auditor_image,
            notes=notes,
            hawk_api_url=remote.hawk_api_url,
        )
        path = write_config(root, label, config)
        try:
            submitted = remote.hawk.submit(path)
        except Exception as ex:
            raise ToolError(f"submission failed: {ex}") from ex
        remote.ledger.add(
            Job(label=label, kind="audit", eval_set_id=submitted, config_path=str(path),
                submitted_at=utcnow(), estimated_usd=estimated_usd, note=note)
        )
        return (
            f"Submitted audit job {label!r} as Hawk eval set {submitted} over {logs_source} "
            f"(items={items}, limit={limit}, samples={sample_ids}, auditor={auditor_model}, grader={grader_model}). "
            f"Reserved ${estimated_usd:.2f}."
        )

    return execute


@tool
def jobs(remote: Remote, root: Path) -> Tool:
    """Status, waiting, collection and stopping of remote jobs."""

    async def execute(action: str, label: str | None = None, wait_minutes: float = 20) -> str:
        """Manage the jobs this investigation launched.

        Args:
            action: "list" (every job and its state), "status" (live per-eval status of
                one job), "wait" (block, without spending tokens, until the job finishes
                or wait_minutes pass), "collect" (download its .eval logs to
                /inputs/jobs/<label>/, record the real cost, release the reservation),
                or "stop" (gracefully stop a running job).
            label: The job, for every action except "list".
            wait_minutes: How long "wait" may block before returning the current state.
        """
        ledger = remote.ledger
        if action == "list":
            if not ledger.jobs:
                return "no remote jobs yet"
            return "\n".join(
                f"{j.label} ({j.kind}) {j.eval_set_id}: {j.status}"
                + (f", collected to /inputs/jobs/{j.label}" if j.collected_to else "")
                + (f", ${j.actual_usd:.2f}" if j.actual_usd is not None else f", reserved ${j.estimated_usd:.2f}")
                for j in ledger.jobs
            )
        if not label or not (job := ledger.get(label)):
            raise ToolError(f"unknown job {label!r}; jobs(action='list') shows them")
        try:
            if action == "status":
                rows = remote.hawk.evals(job.eval_set_id)
            elif action == "wait":
                rows = await asyncio.to_thread(wait_for, remote.hawk, job.eval_set_id, wait_minutes)
            elif action == "stop":
                remote.hawk.stop(job.eval_set_id)
                job.status = "stopped"
                ledger.save()
                return f"stop requested for {label} ({job.eval_set_id})"
            elif action == "collect":
                rows = remote.hawk.evals(job.eval_set_id)
                if not rows or not all(r["status"] in ("success", "error", "cancelled") for r in rows):
                    raise ToolError("job is not finished; use action='wait' first")
                files = await asyncio.to_thread(remote.hawk.download, job.eval_set_id, root / "jobs" / "downloads" / label)
                if not files:
                    raise ToolError("no .eval files were downloaded")
                dest = copy_into_inputs(files, root / "inputs", label)
                cost, usage = usage_cost(files)
                job.actual_usd = cost if cost is not None else job.estimated_usd
                job.collected_to = str(dest)
                job.evals = [dict(r) for r in rows]
                job.status = "success" if all(r["status"] == "success" for r in rows) else "error"
                ledger.save()
                lines = [f"collected {len(files)} log(s) to /inputs/jobs/{label}/"]
                lines += [f"  {r['task']} {r['model']}: {r['status']} {r['samples']}" for r in rows]
                lines.append(
                    f"cost: ${cost:.2f}" if cost is not None else f"cost unknown for some models; reservation ${job.estimated_usd:.2f} kept"
                )
                lines += [f"  {m}: in {u['input']:,} cache_read {u['cache_read']:,} out {u['output']:,}" for m, u in usage.items()]
                return "\n".join(lines)
            else:
                raise ToolError("action must be list, status, wait, collect or stop")
        except ToolError:
            raise
        except Exception as ex:
            raise ToolError(f"hawk error: {ex}") from ex
        if rows:
            job.status = "success" if all(r["status"] == "success" for r in rows) else (
                "error" if any(r["status"] in ("error", "cancelled") for r in rows) and all(r["status"] in ("success", "error", "cancelled") for r in rows) else "running"
            )
            job.evals = [dict(r) for r in rows]
            ledger.save()
        return f"{label} ({job.eval_set_id}): {job.status}\n" + "\n".join(
            f"  {r['task']} {r['model']}: {r['status']} {r['samples']}" for r in rows
        ) if rows else f"{label} ({job.eval_set_id}): no evals listed yet (runner still starting)"

    return execute


async def _continue(state: AgentState, interactive: bool) -> bool | str:
    if not store_as(InvestigationState).published:
        return (
            True
            if state.output.message.tool_calls
            else "Continue the investigation, or write and publish a report explaining the evidence and any limitations."
        )
    if not interactive:
        return False
    return await _operator_turn(state)


@task
def investigate(
    repo: str,
    logs: list[str] | None = None,
    paper: str | None = None,
    docs: list[str] | None = None,
    overview: str = "",
    target_task: str | None = None,
    revision: str | None = None,
    paths: list[str] | None = None,
    output_dir: str = "investigations",
    budget_usd: float = 10,
    enforce_cost_limit: bool = True,
    token_limit: str | int | None = None,
    interactive: bool = False,
    extra_skills: list[str] | None = None,
    hawk_api_url: str | None = None,
    task_package: str | None = None,
    audit_package: str = "git+https://github.com/Generality-Labs/inspect_audit@codex/attempt-review",
    auditor_image: str = "ghcr.io/generality-labs/inspect-audit-auditor@sha256:072e50b2ea1c51e67644e97e08cff052a52a1d661294635e1c3e360d1371b9ee",
    worker_models: list[str] | None = None,
    secrets_file: str | None = None,
    log_bucket: str = "arcadia-impact-generality-inspect",
    aws_profile: str | None = None,
) -> Task:
    """Investigate source and existing logs locally, publish HTML, then discuss.

    Requires Docker and, when interactive, --acp-server. Benchmark execution and
    Hawk job dispatch are deliberately not enabled in this first slice.

    Args:
        repo: Local Git repository or HTTPS Git URL of the benchmark.
        logs: Inspect log files or directories (hardlinked, read-only in the box).
        paper: Local file or URL; a URL is downloaded now (arXiv abs -> pdf).
        docs: Documentation directories to mount read-only (inspect docs, Hawk docs).
        overview: Optional operator steer.
        target_task: The task under audit, e.g. `inspect_evals/simpleqa_verified`.
        revision: Commit to snapshot (default HEAD).
        paths: Repository paths to include in the snapshot (default: everything).
        output_dir: Where the investigation directory is created.
        budget_usd: Dollar allowance for this investigator's own model calls.
        enforce_cost_limit: Enforce `budget_usd` through Inspect's cost limit. Needs a
            price for the model; OpenRouter prices are registered automatically.
        token_limit: Optional Inspect token limit (e.g. "output:500k"); none by default.
        interactive: Wait for the operator over ACP after publishing.
        extra_skills: Additional skill directories to load.
        hawk_api_url: Enable remote work through Hawk at this API. The `hawk` CLI must be
            installed and logged in on this machine; its tools run here, never in the box.
        task_package: pip/git spec of the package providing the audited task, installed
            in every Hawk runner (e.g. git+https://github.com/UKGovernmentBEIS/inspect_evals@<commit>).
        audit_package: git spec of inspect_audit for sample-audit jobs.
        auditor_image: Published auditor image for sample-audit jobs on k8s.
        worker_models: OpenRouter model ids the agent may run (benchmark workers, auditors,
            graders). Prices for these are registered so costs are accounted.
        secrets_file: .env passed to Hawk jobs (OPENROUTER_API_KEY); never read by the agent.
        log_bucket: Hawk's S3 log bucket, for staging supplied logs into an audit job's prefix.
        aws_profile: AWS profile with write access to that bucket (else ambient credentials).
    """
    if not math.isfinite(budget_usd) or budget_usd <= 0:
        raise ValueError("budget_usd must be finite and positive")
    skill_paths = [
        str(ASSETS / "skills" / "investigating"),
        str(ASSETS / "skills" / "writing"),
    ] + [str(SKILLS / name) for name in SUPPORT_SKILLS]
    for path in extra_skills or []:
        resolved = Path(path).expanduser().resolve()
        if not (resolved / "SKILL.md").is_file():
            raise ValueError(f"Expected a skill directory containing SKILL.md: {path}")
        skill_paths.append(str(resolved))
    if enforce_cost_limit or hawk_api_url:
        register_openrouter_costs()
    if hawk_api_url and not task_package:
        raise ValueError("hawk_api_url needs task_package: the package Hawk runners install to run the audited task")
    root = prepare_workspace(
        repo,
        revision,
        paths,
        logs or [],
        paper,
        docs or [],
        overview,
        target_task,
        output_dir,
        budget_usd,
    )

    remote: Remote | None = None
    if hawk_api_url:
        remote = Remote(
            root, hawk_api_url, secrets_file, task_package or "", audit_package, auditor_image,
            worker_models or DEFAULT_WORKERS, log_bucket, aws_profile, budget_usd,
        )
        seed_path = root / "inputs" / "seed.json"
        seed = json.loads(seed_path.read_text())
        seed["remote"] = {
            "hawk": hawk_api_url,
            "task_package": task_package,
            "worker_models": worker_models or DEFAULT_WORKERS,
            "note": "run_benchmark and run_audit submit jobs to Hawk; jobs() waits and collects into /inputs/jobs/<label>/",
        }
        seed_path.write_text(json.dumps(seed, indent=2))
    tools: list[Tool] = [
        bash(timeout=300),
        skill(skill_paths),
        investigation_budget(budget_usd, enforce_cost_limit, remote),
        render_report(),
        view_image(),
        publish_report(str(root)),
    ]
    if remote is not None:
        tools += [
            run_benchmark(remote, target_task, root),
            run_audit(remote, target_task, root),
            jobs(remote, root),
        ]

    async def on_continue(state: AgentState) -> bool | str:
        return await _continue(state, interactive)

    return Task(
        dataset=[
            Sample(
                id="investigation",
                input="Read /inputs/seed.json and invoke the investigating skill. Begin the investigation autonomously.",
            )
        ],
        solver=react(
            name="investigator",
            prompt=PROMPT,
            submit=False,
            tools=tools,
            compaction=CompactionSummary(threshold=0.8),
            on_continue=on_continue,
        ),
        sandbox=("docker", str(root / "compose.yaml")),
        # tool results are truncated at 16KB by default; an inventory of fifty
        # logs or a transcript dump is routinely larger, and a truncated view
        # is what the agent then reasons from
        config=GenerateConfig(max_tool_output=200 * 1024),
        cost_limit=budget_usd if enforce_cost_limit else None,
        token_limit=token_limit,
        metadata={
            "investigation_dir": str(root),
            "interactive": interactive,
            "capabilities": ["repository", "existing_logs", "docs", "quarto_report", "acp"]
            + (["hawk_jobs"] if remote else []),
        },
    )
