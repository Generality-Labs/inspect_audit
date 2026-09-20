"""Finding validation, report publication and shared ACP turn-taking."""
import json
import os
import shutil
from pathlib import Path
from typing import Literal
from uuid import uuid4

from acp.schema import ElicitationSchema, ElicitationStringPropertySchema
from inspect_ai.agent import AgentState
from inspect_ai.tool import Tool, ToolError, tool
from inspect_ai.util import (
    StoreModel,
    request_input,
    sandbox,
    store_as,
)
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter


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
    "construct", "contentvalidity", "dataset", "scaffold", "harness",
    "environment", "grading", "resources", "informativeness",
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
        try:
            destination = save_publication(Path(root))
        except (ValueError, OSError) as ex:
            raise ToolError(str(ex)) from ex
        store_as(InvestigationState).published = str(destination)
        return f"Published {destination / 'report.html'}. Give the operator a concise summary and the report path."

    return execute
