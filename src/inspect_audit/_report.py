"""Layer-2 synthesis: the top-level agent that sits above completed audits.

This is step 4 of the README pipeline ("Synthesize"). The current rung: the
agent works inside a sandbox with the audited run's logs staged at
/report/logs (and inspect-ai + pandas installed, so the log API works in
place), while the operator connects to its head over ACP. The frames layer
and the synthesis skill land on top of this.

Run it interactively:

    inspect eval inspect_audit/report -T logs=<log-dir> \
        --model <model> --acp-server --display none

then attach from another shell with `inspect acp`, or through the web chat
(`python frontend/server.py`; see frontend/README.md).

With no `logs` argument the task degrades to the sandbox-less chat skeleton
(useful for exercising the ACP plumbing without docker).
"""

import atexit
import json
import os
import re
import shutil
import tempfile
from importlib.metadata import version
from pathlib import Path
from typing import Literal
from uuid import uuid4

from acp.schema import ElicitationSchema, ElicitationStringPropertySchema
from inspect_ai import Task
from inspect_ai.agent import AgentState, react
from inspect_ai.dataset import Sample
from inspect_ai.log import list_eval_logs
from inspect_ai.tool import Tool, ToolError, bash, python, skill, tool
from inspect_ai.util import (
    SandboxEnvironmentType,
    StoreModel,
    request_input,
    sandbox,
    store_as,
)
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from . import prompts
from ._agent import SKILLS, SUPPORT_SKILLS
from .containers import COMPOSE, DOCKERFILE

REPORT_ROOT = "/report"

# the synthesis skill lives apart from skills/ because dirs there are
# enumerated as audit items for auditors (see _agent.audit_skills)
REPORT_SKILLS = Path(__file__).parent / "report_skills"




async def _operator_turn(state: AgentState) -> bool | str:
    """Hand the floor to the operator whenever the model stops calling tools.

    react()'s default on_continue nudges the model to keep going, which makes
    a conversational agent chatter to itself. Instead we block on an ACP
    elicitation until the operator replies; their text becomes the next user
    message. Declining or cancelling the form ends the agent.
    """
    if state.output.message.tool_calls:
        return True
    result = await request_input(
        message="Reply to the agent",
        schema=ElicitationSchema(
            properties={
                "message": ElicitationStringPropertySchema(
                    type="string", title="Message"
                )
            },
            required=["message"],
        ),
    )
    if result.outcome == "accepted" and result.content:
        return str(result.content["message"])
    return False


def _staged_logs(logs: str) -> dict[str, str]:
    """Container path -> host path for every log under `logs`.

    Uses `list_eval_logs` (which knows what a log file is) and keeps each
    file's path relative to the log root, so same-named logs in different
    subdirectories cannot silently collide.
    """
    root = Path(logs).resolve()
    if root.is_file():
        return {f"{REPORT_ROOT}/logs/{root.name}": str(root)}
    files = [
        Path(info.name.removeprefix("file://")).resolve()
        for info in list_eval_logs(str(root))
    ]
    if not files:
        raise ValueError(f"No logs found at {logs!r}.")
    return {f"{REPORT_ROOT}/logs/{f.relative_to(root)}": str(f) for f in files}


def _report_sandbox() -> SandboxEnvironmentType:
    """The audit module's generic sandbox, with the log-reading stack installed."""
    stage = Path(tempfile.mkdtemp(prefix="inspect_report_sandbox_"))
    atexit.register(shutil.rmtree, stage, ignore_errors=True)
    requirements = f"inspect-ai=={version('inspect-ai')} pandas pyarrow"
    (stage / "Dockerfile").write_text(DOCKERFILE.format(requirements=requirements))
    compose = stage / "compose.yaml"
    compose.write_text(COMPOSE)
    return ("docker", str(compose))


def report_task(logs: str | None = None) -> Task:
    """Build the synthesis session as an Inspect `Task`.

    Args:
        logs: Log file or directory of logs to stage into the sandbox at
            /report/logs. None runs the sandbox-less chat skeleton.
    """
    if logs is None:
        return Task(
            dataset=[
                Sample(
                    input="Greet the operator in one short sentence and wait "
                    "for direction."
                )
            ],
            solver=react(
                name="report",
                description="Synthesis agent (plumbing-test mode).",
                prompt=prompts.REPORT_CHAT_ONLY,
                on_continue=_operator_turn,
            ),
        )

    files = _staged_logs(logs)
    intro = (
        f"{len(files)} log file(s) are staged at {REPORT_ROOT}/logs. Greet the "
        "operator in one short sentence and wait for direction."
    )
    return Task(
        dataset=[Sample(input=intro, files=files)],
        solver=react(
            name="report",
            description="Synthesis agent over completed audit logs.",
            prompt=prompts.REPORT.format(root=REPORT_ROOT),
            tools=[
                bash(timeout=300),
                python(timeout=300),
                skill(
                    [str(d) for d in sorted(REPORT_SKILLS.iterdir()) if d.is_dir()]
                    + [str(SKILLS / name) for name in SUPPORT_SKILLS]
                ),
            ],
            on_continue=_operator_turn,
        ),
        sandbox=_report_sandbox(),
    )


class InvestigationState(StoreModel):
    """Publication state belongs to the sample, including across compaction."""

    published: str | None = None
    # set when the agent has been told the shared allowance is gone; the next turn
    # ends the sample rather than asking again
    allowance_notified: bool = False


class EvidenceRef(BaseModel):
    """A local primary artifact with a location and optional literal quotation."""

    model_config = ConfigDict(extra="forbid")
    path: str = Field(min_length=1)
    location: str = Field(min_length=1)
    quote: str | None = None


Section = Literal[
    "task",
    "grader",
    "harness_environment",
    "aggregation_limits",
    "agent_behaviour",
    "construction",
]


class Finding(BaseModel):
    """One revisable finding; publication snapshots this same register."""

    model_config = ConfigDict(extra="forbid")
    id: str = Field(min_length=1)
    section: Section
    claim: str = Field(min_length=1)
    status: Literal["hypothesis", "supported", "qualified", "retracted"]
    origin: Literal["historical", "experiment", "source", "audit_limitation"]
    # how much of the reported result this finding puts in question: high means the
    # affected results cannot be trusted, medium that they are noisy or imprecise, low
    # that it is an edge case worth recording. Absent while a finding is a hypothesis.
    severity: Literal["high", "medium", "low"] | None = None
    evidence: list[EvidenceRef]
    reproduce: str = Field(min_length=1)
    limitations: str


def validate_findings(root: Path) -> list[Finding]:
    """Validate shape and file provenance, not the truth of an interpretation."""
    report = root / "work/report"
    findings = TypeAdapter(list[Finding]).validate_json(
        (report / "findings.json").read_text()
    )
    ids = [f.id for f in findings]
    if len(set(ids)) != len(ids):
        raise ValueError("Finding ids must be unique")
    for finding in findings:
        if finding.status in ("supported", "qualified") and not finding.evidence:
            raise ValueError(
                f"{finding.id}: supported/qualified findings require evidence"
            )
        for evidence in finding.evidence:
            path = Path(evidence.path)
            if path.is_absolute():
                if not path.is_relative_to("/inputs"):
                    raise ValueError(
                        f"Evidence must be bundle-relative or under /inputs: {path}"
                    )
                base, relative = root / "inputs", path.relative_to("/inputs")
            else:
                base, relative = report, path
            resolved = (base / relative).resolve()
            if not resolved.is_relative_to(base.resolve()) or not resolved.is_file():
                raise ValueError(
                    f"Evidence file is missing or outside its allowed root: {path}"
                )
    return findings


def save_publication(root: Path) -> Path:
    """Copy a self-contained rendered report outside the agent's writable mount."""
    report = root / "work" / "report"
    if report.is_symlink() or any(p.is_symlink() for p in report.rglob("*")):
        raise ValueError("Report bundles must contain real files, not symlinks")
    for name in ("report.qmd", "report.html", "findings.json"):
        if not (report / name).is_file():
            raise ValueError(f"Missing report artifact: {name}")
    findings = validate_findings(root)
    if (report / "_inputs").exists():
        raise ValueError("_inputs is reserved for publication's input evidence")
    destination = root / "published" / uuid4().hex
    destination.parent.mkdir(exist_ok=True)
    shutil.copytree(
        report,
        destination,
        ignore=shutil.ignore_patterns("__pycache__", ".quarto", "*.pyc"),
    )
    # Keep the agent's single register; rewrite only the published snapshot's
    # input addresses so its evidence survives independently of this workspace.
    for finding in findings:
        for evidence in finding.evidence:
            path = Path(evidence.path)
            if path.is_absolute():
                relative = path.relative_to("/inputs")
                dest = destination / "_inputs" / relative
                dest.parent.mkdir(parents=True, exist_ok=True)
                source = (root / "inputs" / relative).resolve()
                evidence.path = str(dest.relative_to(destination))
                if dest.exists():  # the same input cited by several findings
                    continue
                # hardlink, not copy: a cited .eval log is tens of MB and a
                # bundle citing every log would otherwise duplicate the inputs
                # per published version. inputs are immutable, so sharing is safe
                try:
                    os.link(source, dest)
                except OSError:
                    shutil.copyfile(source, dest)
    (destination / "findings.json").write_text(
        json.dumps([f.model_dump() for f in findings], indent=2)
    )
    return destination


_PROCESS_NARRATION = re.compile(
    r"\b(I|we) (reviewed|inspected|examined|checked|looked at|analy[sz]ed|read|investigated)\b",
    re.IGNORECASE,
)


def lint_report_text(text: str) -> list[str]:
    """Blocking style problems in authored prose: dashes and drafting comments.

    Only the report's own sentences are judged. Tables, code, quoted transcripts and
    the table of contents are stripped before this sees the text, because a report
    that has to reword its evidence to satisfy a style rule is a worse report.
    """
    problems: list[str] = []
    if "\u2014" in text or "\u2013" in text:
        problems.append("em/en dashes present; use a comma or a full stop")
    if "<!--" in text:
        problems.append("drafting comments still present in the document")
    narration = [s for s in _sentences(text) if _PROCESS_NARRATION.search(s)]
    if len(narration) > 3:
        problems.append(
            f"{len(narration)} sentences narrate the process (\"I reviewed…\"); state findings, e.g. {narration[0][:120]!r}"
        )
    return problems


def lint_report_warnings(text: str) -> list[str]:
    """Style notes that do not block publication; the agent sees them in the result."""
    warnings: list[str] = []
    long = [s for s in _sentences(text) if len(s.split()) > 40]
    if long:
        warnings.append(
            f"{len(long)} sentence(s) over 40 words, e.g. {long[0][:120]!r}"
        )
    return warnings


def _sentences(text: str) -> list[str]:
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+", text) if s.strip()]


# what is not the report's own prose: evidence, navigation, and rendered artefacts
_NOT_PROSE = re.compile(
    r"<(script|style|table|pre|code|blockquote|figcaption|nav)\b[^>]*>.*?</\1>",
    re.S | re.I,
)
_TOC = re.compile(r'<(div|aside)[^>]*\bid="(TOC|toc)"[^>]*>.*?</\1>', re.S | re.I)


def _rendered_text(html: str) -> str:
    """Everything the reader sees, tags removed. Used for previews, not for linting."""
    text = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", html, flags=re.S | re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text)


def _prose_text(html: str) -> str:
    """The report's authored sentences: no tables, code, quotations or navigation."""
    text = _TOC.sub(" ", html)
    previous = ""
    while previous != text:  # nested tables and code blocks inside them
        previous = text
        text = _NOT_PROSE.sub(" ", text)
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text)


@tool
def publish_report(root: str) -> Tool:
    """Render and persist a report before entering discussion mode."""

    async def execute() -> str:
        """Render report/report.qmd, save an immutable version, and open discussion."""
        result = await sandbox().exec(
            ["quarto", "render", "/workspace/report/report.qmd", "--to", "html"],
            timeout=300,
        )
        if not result.success:
            raise ToolError(
                f"Report rendering failed:\n{result.stderr}\n{result.stdout}"
            )
        qmd = await sandbox().read_file("/workspace/report/report.qmd")
        html = await sandbox().read_file("/workspace/report/report.html")
        prose = _prose_text(html)
        problems = lint_report_text(prose)
        if "<!--" in qmd:
            problems.append("drafting comments still present in report.qmd")
        if problems:
            raise ToolError(
                "The report does not meet the writing skill yet:\n- "
                + "\n- ".join(dict.fromkeys(problems))
            )
        try:
            destination = save_publication(Path(root))
        except (ValueError, OSError) as ex:
            raise ToolError(str(ex)) from ex
        store_as(InvestigationState).published = str(destination)
        warnings = lint_report_warnings(prose)
        return (
            f"Published {destination / 'report.html'}."
            + (f" Style warnings, not blocking: {'; '.join(warnings)}." if warnings else "")
            + " Give the operator a concise summary and the report path."
        )

    return execute
