"""Local, artifact-first investigation using Inspect's standard agent and ACP."""

import asyncio
import errno
import json
import math
import os
import re
import shutil
import subprocess
import tarfile
import urllib.request
from html.parser import HTMLParser
from importlib.metadata import version
from logging import getLogger
from pathlib import Path
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

from . import prompts
from ._agent import SKILLS, SUPPORT_SKILLS, view_image
from ._jobs import (
    Hawk,
    Job,
    JobLedger,
    Policy,
    copy_into_inputs,
    parse_config,
    slug,
    stage_logs_to_s3,
    task_package_name,
    usage_cost,
    utcnow,
    validate_config,
    wait_for,
    worst_case_usd,
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

# Ours first, then the vendored ones, adapted for a container with no user in it and no
# `hawk` binary. See investigation/skills/VENDORED.md for provenance and what changed.
INVESTIGATION_SKILLS = (
    "investigating",
    "writing",
    "eval-validity-review",
    "investigate-dataset",
    "security-audit-eval",
    "check-trajectories-workflow",
    "eval-report-workflow",
    "read-eval-logs",
    "view-results",
    "debug-stuck-eval",
    "babysit-eval",
)
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
        # the template embeds resources, so every figure in the HTML is a data: URI
        # and its src says nothing. The files behind them are what view_image reads.
        listing = await sandbox().exec(
            ["bash", "-lc", "ls -1 /workspace/report/evidence/*.{png,jpg,jpeg,svg,webp} 2>/dev/null | head -40"],
            timeout=60,
        )
        files = [line for line in (listing.stdout or "").splitlines() if line.strip()]
        return (
            f"Rendered ({len(text)} characters of text, {len(parser.images)} figures).\n"
            + (f"Quarto warnings:\n{warnings}\n" if warnings else "")
            + (
                "Figure files to look at with view_image:\n  " + "\n  ".join(files) + "\n"
                if files
                else "No figure files under /workspace/report/evidence/.\n"
            )
            + f"\n{text[:6000]}"
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
        self.policy = Policy(
            packages=[task_package, audit_package],
            task_names=[task_package_name(task_package), "inspect_audit"],
            models=worker_models,
            auditor_images=[auditor_image],
            hawk_api_url=hawk_api_url,
        )
        self._spend_path = root / "local_spend.json"
        spent = (
            json.loads(self._spend_path.read_text()) if self._spend_path.is_file() else {}
        )
        # a resumed run starts Inspect's usage accounting from zero; what earlier runs
        # of this investigation spent is carried forward from disk
        self.prior_local_usd = float(spent.get("prior_usd", 0.0)) + float(
            spent.get("this_run_usd", 0.0)
        )
        self._sources_path = root / "log_sources.json"
        self.known_sources: set[str] = set(
            json.loads(self._sources_path.read_text()) if self._sources_path.is_file() else []
        ) | {j.eval_set_id for j in self.ledger.jobs}

    def save_sources(self) -> None:
        self._sources_path.write_text(json.dumps(sorted(self.known_sources)))

    def check_model(self, model: str) -> None:
        if model not in self.worker_models:
            raise ToolError(
                f"{model!r} is not an allowed worker model. Allowed: {', '.join(self.worker_models)}"
            )

    def new_eval_set_id(self, label: str) -> str:
        """A fresh id per job. Reusing one makes Hawk resume that set instead."""
        return f"{self.policy.id_prefix}{slug(label)}-{uuid4().hex[:8]}"[:43]

    def model_costs(self) -> tuple[dict[str, dict[str, float]], list[str]]:
        """Prices for the worker models, so the runner can enforce its cost limit.

        The agent may not write these: a job whose prices are its own invention has a
        cost limit that means nothing. They come from the same registry the local
        allowance is accounted with.

        Both the key and the lookup use the name the job will run under. A worker is
        named in a config the way Hawk composes it, provider group then item, so
        `openai/gpt-5.6-luna` under the `openrouter` group is `openrouter/openai/
        gpt-5.6-luna` to Inspect, in the runner's cost table and in its logs. Returns
        the prices and the workers that have none: without a price a cost limit cannot
        bind, so that list is a refusal, not a warning.
        """
        from inspect_ai.model._model_info import get_model_info

        costs: dict[str, dict[str, float]] = {}
        missing: list[str] = []
        for model in self.worker_models:
            qualified = qualified_model_name(model)
            info = get_model_info(qualified)
            cost = info.cost if info else None
            if cost is None or not (cost.input or cost.output):
                missing.append(model)
                continue
            costs[qualified] = {
                "input": cost.input or 0.0,
                "output": cost.output or 0.0,
                "input_cache_read": cost.input_cache_read or 0.0,
                "input_cache_write": cost.input_cache_write or 0.0,
            }
        return costs, missing

    def record_local_spend(self) -> None:
        """Keep this run's own spend on disk, so a resumed investigation inherits it."""
        local = _local_spend()[0]
        if local is None:
            return
        self._spend_path.write_text(
            json.dumps({"prior_usd": self.prior_local_usd, "this_run_usd": local})
        )

    def local_usd(self) -> float:
        """Every dollar this investigation has spent on its own model calls."""
        return self.prior_local_usd + (_local_spend()[0] or 0.0)

    def committed_usd(self) -> float:
        """Spent locally, plus collected remote costs, plus live reservations."""
        return self.local_usd() + self.ledger.actual_usd() + self.ledger.reserved_usd()

    def over_allowance(self) -> str | None:
        """The message to give the agent when the shared allowance is gone, else None."""
        committed = self.committed_usd()
        if committed < self.allowance_usd:
            return None
        return (
            f"The ${self.allowance_usd:.2f} allowance is spent or committed "
            f"(${committed:.2f}: ${self.local_usd():.2f} on your own calls, "
            f"${self.ledger.actual_usd():.2f} measured on remote jobs, "
            f"${self.ledger.reserved_usd():.2f} held against jobs not yet collected)."
        )

    def reserve_and_record(self, job: Job) -> None:
        """Hold a job's worst case against the allowance and write it down, atomically.

        Both halves happen under the ledger's file lock, and the ledger is re-read
        inside it, so two submissions in flight cannot both take the last of the money.
        The job is written as `pending` before anything is sent to Hawk.
        """
        with self.ledger.transaction() as ledger:
            if ledger.get(job.label) is not None:
                raise ToolError(
                    f"a job labelled {job.label!r} already exists; use jobs() on it or choose another name"
                )
            committed = self.committed_usd()
            if committed + job.reserved_usd > self.allowance_usd:
                raise ToolError(
                    f"cannot reserve ${job.reserved_usd:.2f}: ${committed:.2f} of the "
                    f"${self.allowance_usd:.2f} allowance is already spent or reserved. Collect "
                    "finished jobs to release their reservations, or make this job smaller "
                    "(fewer samples, a lower cost_limit)."
                )
            ledger.add(job)

    def settle(self, label: str, **fields: object) -> None:
        """Update one job under the lock; the ledger on disk is the record."""
        with self.ledger.transaction() as ledger:
            job = ledger.get(label)
            if job is None:  # pragma: no cover - only if the file was edited underneath us
                return
            for key, value in fields.items():
                setattr(job, key, value)

    def reconcile(self) -> list[str]:
        """Resolve jobs left `pending` by a lost response or a killed process.

        A submission is written down before it is sent, so a job can be pending when
        Hawk never saw it, or when Hawk took it and the answer never came back. Asking
        Hawk which it was is the only way to know, and getting it wrong either loses a
        running job or launches it twice.
        """
        notes: list[str] = []
        for job in list(self.ledger.jobs):
            if job.status != "pending":
                continue
            try:
                exists = self.hawk.eval_set_exists(job.eval_set_id)
            except Exception as ex:  # network or auth trouble: leave it pending
                notes.append(f"{job.label}: could not reach Hawk to check ({ex})")
                continue
            if exists:
                self.settle(job.label, status="submitted")
                self.known_sources.add(job.eval_set_id)
                self.save_sources()
                notes.append(f"{job.label}: was submitted after all ({job.eval_set_id})")
            else:
                self.settle(job.label, status="failed")
                notes.append(f"{job.label}: never reached Hawk; reservation released")
        return notes


def qualified_model_name(model: str) -> str:
    """The name a worker runs under: the OpenRouter group, then the model's own id."""
    return model if model.startswith("openrouter/") else f"openrouter/{model}"


def _models_named(config: dict[str, object]) -> set[str]:
    """Every model the config will actually construct, qualified as Hawk composes it."""
    named: set[str] = set()
    models = config.get("models")
    groups: list[object] = list(models) if isinstance(models, list) else []
    roles = config.get("model_roles")
    if isinstance(roles, dict):
        groups += list(roles.values())
    for group in groups:
        if not isinstance(group, dict):
            continue
        provider = str(group.get("name", ""))
        for item in group.get("items") or []:
            if isinstance(item, dict) and item.get("name"):
                named.add(f"{provider}/{item['name']}")
    return named


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
            remote.ledger.reload()
            jobs = remote.ledger.jobs
            lines.append(
                f"Remote jobs: {len(jobs)} launched, ${remote.ledger.actual_usd():.2f} measured cost, "
                f"${remote.ledger.reserved_usd():.2f} held against jobs not yet collected"
            )
            for j in jobs:
                cost_text = (
                    f"${j.actual_usd:.2f} spent"
                    if j.actual_usd is not None
                    else f"holds ${j.reserved_usd:.2f} (you estimated ${j.estimated_usd:.2f})"
                )
                lines.append(f"  {j.label} ({j.kind}, {j.eval_set_id}): {j.status}, {cost_text}")
            if remote.ledger.unpriced():
                lines.append(
                    "  collected but unpriced, so their real cost is unknown and their "
                    f"reservation is still held: {', '.join(remote.ledger.unpriced())}"
                )
            committed = (total if not unpriced else 0.0) + remote.ledger.actual_usd() + remote.ledger.reserved_usd()
            lines.append(f"Committed in total: ${committed:.2f} of ${budget_usd:.2f}")
        lines.append(
            "Scope: this investigator's own model calls plus remote jobs it launched. The allowance is shared across both."
        )
        return "\n".join(lines)

    return execute


@tool
def hawk_submit(remote: Remote, root: Path) -> Tool:
    """Submit an eval-set config you wrote to Hawk, after policy checks."""

    async def execute(config: str, estimated_usd: float, note: str = "") -> str:
        """Submit a Hawk eval-set config file from your workspace.

        Write the config yourself (see the investigating skill's examples, and Hawk's
        own documentation under /inputs/docs when the operator supplied it), save it
        under /workspace, and pass its path. It is parsed with Hawk's own schema and then checked against
        this investigation's policy: only the allowed packages, task packages, models,
        images, secrets and environment keys; task arguments that name a model, an
        image, a size or a log source must satisfy the same rules; `cost_limit` is
        required and capped; the job must state how many samples it runs. Anything
        else is refused with the reasons.

        The eval set id and the model prices are set here, not by you: a fresh id per
        job (a reused one makes Hawk resume that set) and the prices the allowance is
        accounted with, so the runner's cost limit means what it says. Your job holds
        its worst case (cost per sample x samples x models x epochs) against the
        allowance until jobs(action="collect").

        Args:
            config: Path of the YAML file in /workspace, e.g. /workspace/jobs/smoke.eval-set.yaml.
            estimated_usd: What you expect this to really cost. Recorded and compared
                with the outcome; the reservation is the worst case, not this number.
            note: Why you are running this; recorded in the ledger.
        """
        if not config.startswith("/workspace/"):
            raise ToolError("config must be a path under /workspace")
        host_path = root / "work" / Path(config).relative_to("/workspace")
        if not host_path.is_file():
            raise ToolError(f"no such file: {config}")
        try:
            data = yaml.safe_load(host_path.read_text())
        except yaml.YAMLError as ex:
            raise ToolError(f"config is not valid YAML: {ex}") from ex
        if not isinstance(data, dict):
            raise ToolError("config must be a YAML mapping")
        if not (estimated_usd > 0):
            raise ToolError("estimated_usd must be positive: say what you expect this to cost")

        problems = validate_config(data, remote.policy, remote.known_sources)
        if problems:
            raise ToolError("config refused:\n- " + "\n- ".join(problems))

        label = str(data["name"]).removeprefix(remote.policy.id_prefix)
        eval_set_id = remote.new_eval_set_id(label)
        data["eval_set_id"] = eval_set_id
        costs, unpriced = remote.model_costs()
        named = _models_named(data)
        blind = sorted(named & {qualified_model_name(m) for m in unpriced})
        if blind:
            raise ToolError(
                f"no registered price for {', '.join(blind)}, so cost_limit could not be "
                "enforced in the runner and the job's spend would be unbounded. Use a model "
                "that is priced, or ask the operator to register a price for this one."
            )
        # every worker's price, not only the ones named here: a task that builds its
        # own grader still charges the same key, and an unpriced model is invisible to
        # the runner's cost limit
        data["model_cost_config"] = costs
        parsed, _ = parse_config(data)
        worst = worst_case_usd(parsed, remote.policy)
        if worst is None:  # pragma: no cover - validate_config already refused this
            raise ToolError("the job does not state its size")

        # the identity is claimed before anything is written: a refused duplicate must
        # not overwrite the config of the job that actually ran under that name
        submitted_path = root / "jobs" / f"{label}.eval-set.yaml"
        job = Job(
            label=label,
            kind="eval-set",
            eval_set_id=eval_set_id,
            config_path=str(submitted_path),
            submitted_at=utcnow(),
            estimated_usd=estimated_usd,
            reserved_usd=worst,
            status="pending",
            note=note,
        )
        remote.reserve_and_record(job)
        if submitted_path.exists():  # pragma: no cover - the ledger already refused this
            raise ToolError(f"{submitted_path.name} already exists; choose another name")
        write_config(root, label, data)

        try:
            returned = await asyncio.to_thread(remote.hawk.submit, submitted_path)
        except Exception as ex:
            # the job is already written down as pending; ask Hawk whether it landed
            notes = await asyncio.to_thread(remote.reconcile)
            raise ToolError(
                f"submission failed: {ex}\n" + ("\n".join(notes) if notes else "")
            ) from ex
        if returned != eval_set_id:  # Hawk renamed it; the ledger follows Hawk
            eval_set_id = returned
        remote.settle(label, status="submitted", eval_set_id=eval_set_id)
        remote.known_sources.add(eval_set_id)
        remote.save_sources()
        return (
            f"Submitted {data['name']!r} as Hawk eval set {eval_set_id}. Reserved "
            f"${worst:.2f}, the most it can spend (you estimated ${estimated_usd:.2f}). "
            f"jobs(action='watch', label='{label}') shows it running; jobs(action='wait', "
            f"label='{label}') blocks until it finishes; jobs(action='collect', ...) brings "
            f"the logs to /inputs/jobs/{label}/ and releases what it did not spend."
        )

    return execute


@tool
def jobs(remote: Remote, root: Path) -> Tool:
    """Watching, reading, waiting on, collecting and stopping remote jobs."""

    async def execute(
        action: str,
        label: str | None = None,
        sample: str | None = None,
        wait_minutes: float = 20,
        limit: int | None = None,
    ) -> str:
        """Watch and manage the Hawk jobs this investigation launched.

        Every action is the `hawk` command of the same name, run here on the operator's
        login, restricted to your own jobs. Reads are always safe; the only actions that
        change anything are "stop" and "collect".

        Args:
            action: What to do.
                "list" - every job of this investigation and its state (no network).
                "evals" - task, model, status and sample counts per eval in the job.
                "watch" - live snapshot: per-task and per-sample phase (waiting, init,
                    running, scoring, completed, errored, limit), retries, scores, and
                    any Kubernetes trouble reason. The first thing to look at when a job
                    is slow or stuck.
                "logs" - tail of the runner's own log: install failures, tracebacks, the
                    reason a job has no evals at all.
                "trace" - the runner's in-flight actions; an `enter` with no matching
                    `exit` is what it is blocked on right now. Running pod only.
                "stacktrace" - live thread stacks of the runner process. Running pod only.
                "status" - the raw monitoring report (pod status, metrics, recent logs).
                "samples" - one line per sample with its id, status and score.
                "transcript" - one sample's full transcript, written to
                    /inputs/jobs/<label>/transcripts/; pass `sample`.
                "transcripts" - every sample's transcript, written to the same place;
                    `limit` caps how many.
                "wait" - block, spending no tokens, until the job finishes or
                    wait_minutes pass.
                "collect" - download the job's .eval logs to /inputs/jobs/<label>/,
                    record the real cost, release the reservation.
                "stop" - gracefully stop a running job; completed samples are scored.
            label: Which job, for every action except "list".
            sample: Sample uuid, for action "transcript" (the "samples" action lists them).
            wait_minutes: How long "wait" may block before returning the current state.
            limit: For "samples" and "transcripts", how many samples to take.
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
        transcripts = root / "inputs" / "jobs" / label / "transcripts"
        try:
            if action == "logs":
                return f"{label} ({job.eval_set_id}) runner log tail:\n" + await asyncio.to_thread(
                    remote.hawk.logs, job.eval_set_id
                )
            if action == "watch":
                return f"{label} ({job.eval_set_id}) live status:\n" + await asyncio.to_thread(
                    remote.hawk.watch, job.eval_set_id
                )
            if action == "trace":
                return f"{label} ({job.eval_set_id}) runner trace:\n" + await asyncio.to_thread(
                    remote.hawk.trace, job.eval_set_id
                )
            if action == "stacktrace":
                return f"{label} ({job.eval_set_id}) runner stacks:\n" + await asyncio.to_thread(
                    remote.hawk.stacktrace, job.eval_set_id
                )
            if action == "status":
                return f"{label} ({job.eval_set_id}) monitoring report:\n" + await asyncio.to_thread(
                    remote.hawk.status, job.eval_set_id
                )
            if action == "samples":
                rows_json = await asyncio.to_thread(
                    remote.hawk.samples, job.eval_set_id, limit or 500
                )
                if not rows_json:
                    return f"{label}: no samples listed yet"
                out = [f"{len(rows_json)} sample(s) in {job.eval_set_id}:"]
                for row in rows_json:
                    out.append(
                        f"  {row.get('uuid', '?')} id={row.get('id', '')} "
                        f"epoch={row.get('epoch', '')} {row.get('status', '')} "
                        f"scores={row.get('scores', '')}"
                    )
                return "\n".join(out)
            if action == "transcript":
                if not sample:
                    raise ToolError("action='transcript' needs sample=<uuid> from jobs(action='samples')")
                # a sample uuid addresses any sample in the deployment, so membership in
                # this job is checked here rather than trusted from the argument
                known = await asyncio.to_thread(remote.hawk.samples, job.eval_set_id, 1000)
                if sample not in {str(row.get("uuid")) for row in known}:
                    raise ToolError(
                        f"sample {sample!r} is not in job {label!r}; jobs(action='samples', "
                        f"label='{label}') lists the ones you can read"
                    )
                path = await asyncio.to_thread(remote.hawk.transcript, sample, transcripts)
                return (
                    f"wrote /inputs/jobs/{label}/transcripts/{path.name} "
                    f"({path.stat().st_size:,} bytes). Read it with your own tools."
                )
            if action == "transcripts":
                files = await asyncio.to_thread(
                    remote.hawk.transcripts, job.eval_set_id, transcripts, limit
                )
                if not files:
                    raise ToolError("no transcripts were written")
                return (
                    f"wrote {len(files)} transcript(s) to /inputs/jobs/{label}/transcripts/: "
                    + ", ".join(f.name for f in files[:10])
                    + (" …" if len(files) > 10 else "")
                )
            if action == "evals":
                rows = remote.hawk.evals(job.eval_set_id)
            elif action == "wait":
                rows = await asyncio.to_thread(wait_for, remote.hawk, job.eval_set_id, wait_minutes)
            elif action == "stop":
                remote.hawk.stop(job.eval_set_id)
                remote.settle(label, status="stopped")
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
                # an unpriced model leaves the real cost unknown: recording the
                # estimate here would turn a guess into a measurement, so the
                # reservation stands instead
                remote.settle(
                    label,
                    actual_usd=cost,
                    collected_to=str(dest),
                    evals=[dict(r) for r in rows],
                    status="success" if all(r["status"] == "success" for r in rows) else "error",
                )
                job = remote.ledger.get(label) or job
                lines = [f"collected {len(files)} log(s) to /inputs/jobs/{label}/"]
                lines += [f"  {r['task']} {r['model']}: {r['status']} {r['samples']}" for r in rows]
                lines.append(
                    f"cost: ${cost:.2f}, reservation of ${job.reserved_usd:.2f} released"
                    if cost is not None
                    else f"cost unknown: a model in this job has no registered price, so the "
                    f"${job.reserved_usd:.2f} reservation stays held"
                )
                lines += [f"  {m}: in {u['input']:,} cache_read {u['cache_read']:,} out {u['output']:,}" for m, u in usage.items()]
                return "\n".join(lines)
            else:
                raise ToolError(
                    "action must be list, evals, watch, logs, trace, stacktrace, status, "
                    "samples, transcript, transcripts, wait, collect or stop"
                )
        except ToolError:
            raise
        except Exception as ex:
            raise ToolError(f"hawk error: {ex}") from ex
        if rows:
            status = "success" if all(r["status"] == "success" for r in rows) else (
                "error" if any(r["status"] in ("error", "cancelled") for r in rows) and all(r["status"] in ("success", "error", "cancelled") for r in rows) else "running"
            )
            # every write goes through the lock: a bare save() here would rewrite the
            # whole file from a stale copy and could drop another process's reservation
            remote.settle(label, status=status, evals=[dict(r) for r in rows])
            job.status, job.evals = status, [dict(r) for r in rows]
        return f"{label} ({job.eval_set_id}): {job.status}\n" + "\n".join(
            f"  {r['task']} {r['model']}: {r['status']} {r['samples']}" for r in rows
        ) if rows else f"{label} ({job.eval_set_id}): no evals listed yet (runner still starting)"

    return execute


async def _continue(
    state: AgentState, interactive: bool, remote: Remote | None = None
) -> bool | str:
    over = remote.over_allowance() if remote is not None else None
    if over is not None:
        # Inspect's cost limit only sees this agent's own calls, so a run whose
        # children hold most of the allowance would otherwise carry on spending
        # locally as though the money were still there
        published = store_as(InvestigationState).published
        if published:
            return False
        return (
            f"{over} Publish the report now with what you have, and say in it what you "
            "could not finish and why."
        )
    if not store_as(InvestigationState).published:
        return (
            True
            if state.output.message.tool_calls
            else "Continue the investigation, or write and publish a report explaining the evidence and any limitations."
        )
    if not interactive:
        return False
    return await _operator_turn(state)


def _resumable(resume: str) -> Path:
    """An existing investigation directory, with its inputs, workspace and ledger."""
    root = Path(resume).expanduser().resolve()
    if not (root / "inputs" / "seed.json").is_file() or not (root / "work").is_dir():
        raise ValueError(
            f"{resume} is not an investigation directory: it has no inputs/seed.json and work/"
        )
    return root


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
    resume: str | None = None,
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
        resume: An existing investigation directory to carry on in, instead of creating
            one. Its inputs, workspace, journal and job ledger are reused, supplied logs
            are not staged again, and jobs left pending by an interrupted run are
            reconciled with Hawk before the agent starts.
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
    skill_paths = [str(ASSETS / "skills" / name) for name in INVESTIGATION_SKILLS] + [
        str(SKILLS / name) for name in SUPPORT_SKILLS
    ]
    for path in extra_skills or []:
        resolved = Path(path).expanduser().resolve()
        if not (resolved / "SKILL.md").is_file():
            raise ValueError(f"Expected a skill directory containing SKILL.md: {path}")
        skill_paths.append(str(resolved))
    if enforce_cost_limit or hawk_api_url:
        register_openrouter_costs()
    if hawk_api_url and not task_package:
        raise ValueError("hawk_api_url needs task_package: the package Hawk runners install to run the audited task")
    resumed = _resumable(resume) if resume else None
    root = resumed or prepare_workspace(
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
        # the supplied logs are staged by us, once, at setup: the agent gets no S3
        # capability, and a resumed investigation reuses what is already up there
        staged_logs: str | None = (seed.get("remote") or {}).get("supplied_logs")
        if logs and (root / "inputs" / "logs").is_dir() and not staged_logs:
            eval_set_id = f"inv-inputs-{root.name[:8]}"
            staged_logs = stage_logs_to_s3(root / "inputs" / "logs", log_bucket, eval_set_id, aws_profile)
            remote.known_sources.add(eval_set_id)
            remote.save_sources()
        if resumed:
            for note in remote.reconcile():
                logger.info("resume: %s", note)
        seed["remote"] = {
            "hawk": hawk_api_url,
            "task_package": task_package,
            "worker_models": worker_models or DEFAULT_WORKERS,
            "audit_package": audit_package,
            "auditor_image": auditor_image,
            "supplied_logs": staged_logs,
            "note": (
                "write an eval-set config under /workspace and hawk_submit it. To audit the "
                "supplied logs, pass supplied_logs as the audit task's logs argument; to audit a "
                "job you ran, use hawk:<its eval set id>. Do not set eval_set_id: submission "
                "assigns a fresh one, and logs are fetched through the Hawk API, so a job reads "
                "them whatever its own id is. Every config states cost_limit, the dollars a "
                "single sample may spend, and its size; the two decide what the job holds "
                "against the allowance. jobs() watches, waits, collects into "
                "/inputs/jobs/<label>/ and shows runner logs, traces and transcripts."
            ),
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
        tools += [hawk_submit(remote, root), jobs(remote, root)]

    async def on_continue(state: AgentState) -> bool | str:
        if remote is not None:
            remote.record_local_spend()
        return await _continue(state, interactive, remote)

    return Task(
        dataset=[
            Sample(
                id="investigation",
                input="Read /inputs/seed.json and invoke the investigating skill. Begin the investigation autonomously.",
            )
        ],
        solver=react(
            name="investigator",
            prompt=prompts.INVESTIGATE,
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
