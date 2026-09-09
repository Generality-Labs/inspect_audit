"""Local, artifact-first investigation using Inspect's standard agent and ACP."""

import errno
import json
import math
import os
import shutil
import subprocess
from importlib.metadata import version
from pathlib import Path
from uuid import uuid4

import yaml
from inspect_ai import Task, task
from inspect_ai.agent import AgentState, react
from inspect_ai.dataset import Sample
from inspect_ai.log import list_eval_logs
from inspect_ai.model import CompactionSummary
from inspect_ai.model._model import sample_model_usage
from inspect_ai.tool import Tool, bash, skill, tool
from inspect_ai.util import store_as

from ._agent import SKILLS, SUPPORT_SKILLS
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

ASSETS = Path(__file__).parent / "investigation"
PROMPT = (ASSETS / "prompt.md").read_text()


def _snapshot_repo(repo: str, revision: str | None, inputs: Path) -> dict[str, str]:
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
        subprocess.run(
            [
                "git",
                "-C",
                str(source),
                "archive",
                "--format=tar",
                f"--output={inputs / 'source.tar'}",
                commit,
            ],
            check=True,
        )
        return {
            "repo": str(source.resolve()),
            "revision": commit,
            "source": "committed snapshot; excludes working-tree changes and submodule contents",
        }
    if not repo.startswith("https://"):
        raise ValueError("repo must be a local Git repository or an HTTPS Git URL")
    return {
        "repo": repo,
        "revision": revision or "HEAD",
        "source": "clone at setup; resolved commit recorded in workspace",
    }


def prepare_workspace(
    repo: str,
    revision: str | None,
    logs: list[str],
    paper: str | None,
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
    seed: dict[str, object] = dict(_snapshot_repo(repo, revision, inputs))
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
            dest = inputs / "logs" / relative
            dest.parent.mkdir(parents=True, exist_ok=True)
            try:
                os.link(file, dest)
            except OSError as ex:
                if ex.errno != errno.EXDEV:
                    raise
                shutil.copyfile(file, dest)
            log_index.append(
                {"source": str(file), "staged": f"/inputs/logs/{relative}"}
            )
    if paper and Path(paper).expanduser().is_file():
        paper_path = Path(paper).expanduser().resolve()
        (inputs / "paper").mkdir()
        shutil.copyfile(paper_path, inputs / "paper" / paper_path.name)
        paper = f"/inputs/paper/{paper_path.name}"
    seed.update(
        task=target_task,
        overview=overview,
        paper=paper,
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


@tool(name="budget")
def investigation_budget(budget_usd: float, enforce_cost_limit: bool = False) -> Tool:
    """Expose Inspect's accounting without inventing a separate pricing system."""

    async def execute() -> str:
        """Show the investigation allowance, Inspect's recorded cost and token usage."""
        usage = sample_model_usage()
        unpriced = [name for name, value in usage.items() if value.total_cost is None]
        known = sum(
            value.total_cost for value in usage.values() if value.total_cost is not None
        )
        spent = None if unpriced else known
        return json.dumps(
            {
                "allowance_usd": budget_usd,
                "inspect_recorded_usd": spent,
                "remaining_usd": None if spent is None else max(0, budget_usd - spent),
                "known_cost_subtotal_usd": known,
                "unpriced_models": unpriced,
                "cost_limit_enforced": enforce_cost_limit,
                "model_usage": {k: v.model_dump() for k, v in usage.items()},
                "scope": "Local investigator model calls only. Requires Inspect model pricing; unknown prices are not evidence of free usage. No remote jobs are enabled in this version.",
            }
        )

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
    overview: str = "",
    target_task: str | None = None,
    revision: str | None = None,
    output_dir: str = "investigations",
    budget_usd: float = 10,
    interactive: bool = False,
    enforce_cost_limit: bool = False,
    extra_skills: list[str] | None = None,
) -> Task:
    """Investigate source and existing logs locally, publish HTML, then discuss.

    Requires Docker and, when interactive, --acp-server. Benchmark execution and
    Hawk job dispatch are deliberately not enabled in this first slice.
    Local Git inputs use the specified commit (HEAD by default), not dirty files.
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
    root = prepare_workspace(
        repo, revision, logs or [], paper, overview, target_task, output_dir, budget_usd
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
                publish_report(str(root)),
            ],
            compaction=CompactionSummary(threshold=0.8),
            on_continue=on_continue,
        ),
        sandbox=("docker", str(root / "compose.yaml")),
        cost_limit=budget_usd if enforce_cost_limit else None,
        token_limit="output:500k",
        metadata={
            "investigation_dir": str(root),
            "interactive": interactive,
            "capabilities": ["repository", "existing_logs", "quarto_report", "acp"],
        },
    )
