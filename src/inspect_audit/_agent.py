"""The auditing agent, and the verdict it submits."""

import json
from pathlib import Path

from inspect_ai.agent import Agent, AgentSubmit, agent, react
from inspect_ai.scorer import Score, Scorer, Target, scorer
from inspect_ai.solver import TaskState
from inspect_ai.tool import Tool, ToolError, bash, skill, tool
from inspect_ai.util import StoreModel, store_as
from pydantic import Field

from ._prompt import audit_prompt

__all__ = ["GRADES", "Verdict", "audit_agent", "audit_items", "audit_skills", "verdict"]

SKILLS = Path(__file__).parent / "skills"

# Skills that support the work rather than defining it, vendored from Meridian's
# inspect-skills. An auditor is never asked to investigate one of these.
SUPPORT_SKILLS = ("reading-logs", "analyzing-logs", "map-inspect-packages")

GRADES = ("CORRECT", "INCORRECT", "ALTERNATIVES", "UNVERIFIABLE")


def audit_items() -> list[str]:
    """Audit items that can be investigated, one skill each."""
    return [
        path.name
        for path in sorted(SKILLS.iterdir())
        if path.is_dir() and path.name not in SUPPORT_SKILLS
    ]


def audit_skills() -> list[str]:
    """Skill directories available to an auditor."""
    return [str(path) for path in sorted(SKILLS.iterdir()) if path.is_dir()]


class Verdict(StoreModel):
    """What an auditor concluded, recorded where a scorer can read it.

    The store rather than the completion: `react` strips the submit tool call from the
    message history by default, and a run that hits a token or cost limit can end with
    an empty completion — but the store survives both.
    """

    grade: str | None = Field(default=None)
    evidence: str | None = Field(default=None)
    sources: list[str] = Field(default_factory=list)
    tried: str | None = Field(default=None)
    remarks: str | None = Field(default=None)


@tool
def submit_grade() -> Tool:
    async def execute(
        grade: str,
        evidence: str,
        sources: list[str],
        tried: str,
        remarks: str,
    ) -> str:
        """Submit your audit of this item.

        Args:
            grade: One of CORRECT, INCORRECT, ALTERNATIVES, UNVERIFIABLE.
            evidence: What the evidence is, and what it establishes.
            sources: URLs or citations you actually read, one per defensible answer.
            tried: What you did to try to break the item, including what failed.
            remarks: What you actually think, including anything you were not asked about.
        """
        if grade not in GRADES:
            # A ToolError is fed back to the model as a recoverable error, so a
            # malformed grade becomes a retry rather than a lost audit.
            raise ToolError(f"grade must be one of {', '.join(GRADES)}, got {grade!r}")

        submitted = store_as(Verdict)
        submitted.grade = grade
        submitted.evidence = evidence
        submitted.sources = sources
        submitted.tried = tried
        submitted.remarks = remarks
        return json.dumps({"grade": grade, "sources": sources})

    return execute


@agent
def audit_agent(items: list[str] | None = None, model: str | None = None) -> Agent:
    """An auditor: a react loop with the audit skills and a shell in the item's sandbox.

    Args:
        items: Audit items to investigate (defaults to all of them). Named in the
            system message, because an auditor that is not told what it is looking for
            will pick whichever skill looks most relevant to the files in front of it.
        model: Model to audit with (defaults to the evaluated model).
    """
    return react(
        name="auditor",
        description="Audits one benchmark item and submits a grade.",
        prompt=audit_prompt(items or audit_items()),
        tools=[bash(timeout=180), skill(audit_skills())],
        model=model,
        submit=AgentSubmit(tool=submit_grade(), name="submit_grade", keep_in_messages=True),
    )


@scorer(metrics=[])
def verdict() -> Scorer:
    """Surface the auditor's grade as the audit's score."""

    async def score(state: TaskState, target: Target) -> Score:
        submitted = state.store_as(Verdict)
        return Score(
            value=submitted.grade or "NO_VERDICT",
            answer=submitted.grade,
            explanation=submitted.evidence,
            metadata={
                "sources": submitted.sources,
                "tried": submitted.tried,
                "remarks": submitted.remarks,
            },
        )

    return score
