"""Local, artifact-first investigation using Inspect's standard agent and ACP."""

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
from uuid import uuid4

import yaml
from inspect_ai import Task, task
from inspect_ai.agent import AgentState, react
from inspect_ai.dataset import Sample
from inspect_ai.log import list_eval_logs
from inspect_ai.model import CompactionSummary, ModelCost, ModelInfo, set_model_info
from inspect_ai.model._model import sample_model_usage
from inspect_ai.tool import Tool, ToolError, bash, skill, tool
from inspect_ai.util import sandbox, store_as

from ._agent import SKILLS, SUPPORT_SKILLS, view_image
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


@tool(name="budget")
def investigation_budget(budget_usd: float, enforce_cost_limit: bool) -> Tool:
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
        lines.append(
            "Scope: this investigator's own model calls. No remote jobs exist in this version."
        )
        return "\n".join(lines)

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
    """
    if not math.isfinite(budget_usd) or budget_usd <= 0:
        raise ValueError("budget_usd must be finite and positive")
    skill_paths = [str(ASSETS / "skills" / "investigating")] + [
        str(SKILLS / name) for name in SUPPORT_SKILLS
    ]
    for path in extra_skills or []:
        resolved = Path(path).expanduser().resolve()
        if not (resolved / "SKILL.md").is_file():
            raise ValueError(f"Expected a skill directory containing SKILL.md: {path}")
        skill_paths.append(str(resolved))
    if enforce_cost_limit:
        register_openrouter_costs()
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
            tools=[
                bash(timeout=300),
                skill(skill_paths),
                investigation_budget(budget_usd, enforce_cost_limit),
                render_report(),
                view_image(),
                publish_report(str(root)),
            ],
            compaction=CompactionSummary(threshold=0.8),
            on_continue=on_continue,
        ),
        sandbox=("docker", str(root / "compose.yaml")),
        cost_limit=budget_usd if enforce_cost_limit else None,
        token_limit=token_limit,
        metadata={
            "investigation_dir": str(root),
            "interactive": interactive,
            "capabilities": ["repository", "existing_logs", "docs", "quarto_report", "acp"],
        },
    )
