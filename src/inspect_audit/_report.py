"""Finding validation, report publication and shared ACP turn-taking."""

import json
import os
import shutil
from pathlib import Path
from typing import Literal
from uuid import uuid4

from acp.schema import ElicitationSchema, ElicitationStringPropertySchema
from inspect_ai.agent import AgentState
from inspect_ai.scorer import Score, Scorer, Target, frequency, scorer
from inspect_ai.solver import TaskState
from inspect_ai.tool import Tool, ToolError, tool
from inspect_ai.util import (
    StoreModel,
    request_input,
    sandbox,
    store_as,
)
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter


async def operator_turn(state: AgentState) -> bool | str:
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
            properties={"message": ElicitationStringPropertySchema(type="string", title="Message")},
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
    # how the investigation ended, for the outcome scorer: published_complete,
    # published_incomplete (short of the operator's required coverage), blocked
    outcome: str | None = None
    outcome_reasons: list[str] = Field(default_factory=list)


def assessed_fraction(coverage_path: Path) -> tuple[float, str] | None:
    """The share of the question population with a label, and a readable count."""
    if not coverage_path.is_file():
        return None
    coverage = json.loads(coverage_path.read_text())
    denominator = int(coverage.get("denominator") or 0)
    if not denominator:
        return 0.0, "0 questions"
    counts = coverage.get("counts") or {}
    assessed = denominator - int(counts.get("NOT_ASSESSED", denominator))
    return assessed / denominator, f"{assessed}/{denominator} questions assessed"


def record_outcome(report: Path, required_coverage: float | None) -> tuple[str, list[str]]:
    """Whether a publication delivered what the operator asked for."""
    if required_coverage is None:
        return "published_complete", ["no coverage requirement was set"]
    measured = assessed_fraction(report / "coverage.json")
    if measured is None:
        return "published_incomplete", [
            f"{required_coverage:.0%} question coverage was required and the report has no coverage.json"
        ]
    fraction, count = measured
    if fraction + 1e-9 < required_coverage:
        return "published_incomplete", [f"{count}; {required_coverage:.0%} was required"]
    return "published_complete", [count]


class EvidenceRef(BaseModel):
    """A local primary artifact with a location and optional literal quotation."""

    model_config = ConfigDict(extra="forbid")
    path: str = Field(min_length=1)
    location: str = Field(min_length=1)
    quote: str | None = None


Section = Literal[
    "construct",
    "contentvalidity",
    "dataset",
    "scaffold",
    "harness",
    "environment",
    "grading",
    "resources",
    "informativeness",
]


class Finding(BaseModel):
    """One revisable finding; publication snapshots this same register."""

    model_config = ConfigDict(extra="forbid")
    id: str = Field(min_length=1)
    section: Section
    claim: str = Field(min_length=1)
    status: Literal["hypothesis", "supported", "qualified", "retracted"]
    origin: Literal["historical", "experiment", "source", "audit_limitation"]
    # Apply the framework scale to this finding's consequence; the dimension
    # assessment remains a separate synthesis, not the maximum finding severity.
    severity: Literal["Minor", "Major", "Critical"] | None = None
    evidence: list[EvidenceRef]
    reproduce: str = Field(min_length=1)
    limitations: str


def validate_findings(root: Path) -> list[Finding]:
    """Validate shape and file provenance, not the truth of an interpretation."""
    report = root / "work/report"
    findings = TypeAdapter(list[Finding]).validate_json((report / "findings.json").read_text())
    ids = [f.id for f in findings]
    if len(set(ids)) != len(ids):
        raise ValueError("Finding ids must be unique")
    for finding in findings:
        if finding.status in ("supported", "qualified") and not finding.evidence:
            raise ValueError(f"{finding.id}: supported/qualified findings require evidence")
        for evidence in finding.evidence:
            path = Path(evidence.path)
            if path.is_absolute():
                if not path.is_relative_to("/inputs"):
                    raise ValueError(f"Evidence must be bundle-relative or under /inputs: {path}")
                base, relative = root / "inputs", path.relative_to("/inputs")
            else:
                base, relative = report, path
            resolved = (base / relative).resolve()
            if not resolved.is_relative_to(base.resolve()) or not resolved.is_file():
                raise ValueError(f"Evidence file is missing or outside its allowed root: {path}")
    return findings


def cited_inputs(root: Path) -> list[str]:
    """The /inputs paths the current findings cite, for fetching before validation."""
    path = root / "work/report/findings.json"
    try:
        records = json.loads(path.read_text())
    except (OSError, ValueError):
        return []
    return sorted(
        {
            str(evidence.get("path"))
            for finding in records
            if isinstance(finding, dict)
            for evidence in finding.get("evidence") or []
            if isinstance(evidence, dict) and str(evidence.get("path", "")).startswith("/inputs/")
        }
    )


def save_publication(root: Path) -> Path:
    """Copy a self-contained rendered report outside the agent's writable mount."""
    report = root / "work" / "report"
    if report.is_symlink() or any(p.is_symlink() for p in report.rglob("*")):
        raise ValueError("Report bundles must contain real files, not symlinks")
    for name in (
        "report.tex",
        "Findings.tex",
        "metadata.tex",
        "assessments.tex",
        "report.pdf",
        "findings.json",
    ):
        if not (report / name).is_file():
            raise ValueError(f"Missing report artifact: {name}")
    findings = validate_findings(root)
    if (report / "framework/auditframework.sty").exists():
        from ._assessment import assessment_latex, validate_latex_structure

        assessment_latex(report)
        validate_latex_structure(
            report, [(f.id, f.section) for f in findings if f.status in ("supported", "qualified")]
        )
    if (report / "_inputs").exists():
        raise ValueError("_inputs is reserved for publication's input evidence")
    destination = root / "published" / uuid4().hex
    destination.parent.mkdir(exist_ok=True)
    shutil.copytree(
        report,
        destination,
        ignore=shutil.ignore_patterns(
            "__pycache__",
            ".quarto",
            "*.pyc",
            "*.aux",
            "*.log",
            "*.fls",
            "*.fdb_latexmk",
            "*.out",
            "preview",
        ),
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


def _prepare_report(root: str) -> None:
    from ._assessment import assessment_latex, validate_latex_structure

    report = Path(root) / "work/report"
    findings = validate_findings(Path(root))
    if (report / "framework/auditframework.sty").exists():
        (report / "assessments.tex").write_text(assessment_latex(report))
        validate_latex_structure(
            report, [(f.id, f.section) for f in findings if f.status in ("supported", "qualified")]
        )


@tool
def check_report(root: str) -> Tool:
    """Validate structured results and generate tables for review before publication."""

    async def execute() -> str:
        """Check findings, assessments and totals; update report/assessments.tex."""
        from ._investigation_workspace import pull, push_assessments

        await pull(Path(root))
        try:
            _prepare_report(root)
        except (ValueError, OSError, KeyError, TypeError) as ex:
            raise ToolError(f"Report validation failed: {ex}") from ex
        await push_assessments(Path(root))
        return "Records validated and tables generated. Compile with latexmk -pdf -interaction=nonstopmode -halt-on-error report.tex from /workspace/report. Render all pages with pdftoppm, inspect them with view_image, and fix layout before publishing."

    return execute


@tool
def publish_report(root: str, required_coverage: float | None = None) -> Tool:
    """Render and persist a report before entering discussion mode."""

    async def execute() -> str:
        """Compile the GL LaTeX report, save an immutable PDF/source bundle, and open discussion."""
        from ._investigation_workspace import (
            fetch_inputs_files,
            persist,
            pull,
            push_assessments,
            remote_workspace,
        )

        await pull(Path(root))
        try:
            _prepare_report(root)
        except (ValueError, OSError, KeyError, TypeError) as ex:
            raise ToolError(f"Report assessment validation failed: {ex}") from ex
        await push_assessments(Path(root))
        result = await sandbox().exec(
            [
                "latexmk",
                "-r",
                "/workspace/report/.latexmkrc",
                "-cd",
                "-pdf",
                "-interaction=nonstopmode",
                "-halt-on-error",
                "/workspace/report/report.tex",
            ],
            timeout=300,
        )
        if not result.success:
            raise ToolError(f"Report rendering failed:\n{result.stderr}\n{result.stdout}")
        await pull(Path(root))
        # cited /inputs evidence the host lacks (a resumed run's collected logs are in
        # the restored box, not in the fresh runner's scratch directory)
        await fetch_inputs_files(Path(root), cited_inputs(Path(root)))
        try:
            destination = save_publication(Path(root))
        except (ValueError, OSError) as ex:
            raise ToolError(str(ex)) from ex
        investigation = store_as(InvestigationState)
        investigation.outcome, investigation.outcome_reasons = record_outcome(
            destination, required_coverage
        )
        shortfall = (
            f" Recorded as INCOMPLETE: {'; '.join(investigation.outcome_reasons)}. Say so plainly "
            "in the summary; publishing again after more coverage replaces this outcome."
            if investigation.outcome == "published_incomplete"
            else ""
        )
        if remote_workspace(Path(root)):
            durable = await persist(Path(root), destination, f"published/{destination.name}")
            investigation.published = durable
            return f"Published {durable}/report.pdf.{shortfall} Give the operator a concise summary and the report path."
        investigation.published = str(destination)
        return f"Published {destination / 'report.pdf'}.{shortfall} Give the operator a concise summary and the report path."

    return execute


@tool
def report_blocker() -> Tool:
    """End the investigation as blocked, when our own setup stops the operator's ask."""

    async def execute(reason: str) -> str:
        """Stop the investigation because something outside the benchmark prevents the work.

        Use this when a deterministic failure in the audit setup (not in the benchmark)
        makes the operator's primary request impossible: child jobs cannot start, the
        evidence cannot be read, a required tool is missing. Publishing a polished
        report around that gap would record a failed run as a success. The run ends
        immediately and is scored as blocked, with your reason.

        Args:
            reason: What is broken, the evidence for it, and what would unblock it.
        """
        if not reason.strip():
            raise ToolError("say what is blocking the investigation and what would unblock it")
        investigation = store_as(InvestigationState)
        investigation.outcome = "blocked"
        investigation.outcome_reasons = [reason.strip()]
        return "Recorded as blocked. The investigation ends now; the operator sees your reason."

    return execute


OUTCOMES = ["published_complete", "published_incomplete", "blocked", "unpublished"]


@scorer(metrics=[frequency(categories=OUTCOMES)])
def investigation_outcome() -> Scorer:
    """How the investigation ended, so a run that delivered nothing does not read as success."""

    async def score(state: TaskState, target: Target) -> Score:
        investigation = state.store_as(InvestigationState)
        outcome = investigation.outcome or (
            "published_complete" if investigation.published else "unpublished"
        )
        return Score(
            value=outcome,
            explanation="; ".join(investigation.outcome_reasons) or None,
            metadata={
                "published": investigation.published,
                "reasons": investigation.outcome_reasons,
            },
        )

    return score
