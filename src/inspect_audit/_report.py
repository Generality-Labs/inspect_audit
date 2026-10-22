"""Investigation outcomes, job provenance and shared ACP turn-taking."""

import json
from pathlib import Path
from typing import Any

from acp.schema import ElicitationSchema, ElicitationStringPropertySchema
from inspect_ai.agent import AgentState
from inspect_ai.scorer import Score, Scorer, Target, frequency, scorer
from inspect_ai.solver import TaskState
from inspect_ai.tool import Tool, ToolError, tool
from inspect_ai.util import (
    StoreModel,
    request_input,
    store_as,
)
from pydantic import Field


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


def log_address(root: Path, relative: Path, jobs: dict[str, str]) -> str:
    """Where a log under /inputs came from: its job's address or the operator's source.

    Two places put logs there: `jobs(action="collect")` into jobs/<label>/, with
    `jobs` mapping each label to its run's address, and preparation into logs/<n>/.
    """
    parts = list(relative.parts)
    if not parts:
        return "/inputs"
    if parts[0] == "jobs" and len(parts) >= 3 and parts[1] in jobs:
        return f"{jobs[parts[1]].rstrip('/')}/{parts[-1]}"
    if parts[0] == "logs":
        seed_path = root / "inputs" / "seed.json"
        seed = json.loads(seed_path.read_text()) if seed_path.is_file() else {}
        staged = f"/inputs/{relative.as_posix()}"
        for entry in seed.get("logs") or []:
            if entry.get("staged") == staged:
                return str(entry["source"])
    return f"/inputs/{relative.as_posix()}"


def job_addresses() -> dict[str, str]:
    """Each launched job's label and the address its logs live at, from the store."""
    record = store_as(InvestigationRecord)
    return {job["label"]: job["address"] for job in record.jobs if job.get("address")}


class InvestigationRecord(StoreModel):
    """The jobs this investigation launched. The store is checkpointed, so they survive."""

    jobs: list[dict[str, Any]] = Field(default_factory=list)


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
