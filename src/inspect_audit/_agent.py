"""The auditing agent, and the verdict it submits."""

import json
from pathlib import Path

from inspect_ai.agent import Agent, AgentSubmit, agent, react
from inspect_ai.scorer import Score, Scorer, Target, scorer
from inspect_ai.solver import TaskState
from inspect_ai.tool import Tool, ToolError, bash, skill, tool
from inspect_ai.util import StoreModel, store_as
from pydantic import BaseModel, Field

from ._prompt import audit_prompt

__all__ = [
    "GRADES",
    "Evidence",
    "Verdict",
    "audit_agent",
    "audit_items",
    "audit_skills",
    "verdict",
]

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


class Evidence(BaseModel):
    """One piece of evidence: what a source actually says, and which source said it.

    A quote rather than a summary. An auditor that could not read a source cannot
    write this field, which is the point: a conclusion asserted over an unread source
    has nothing to paste here.
    """

    quote: str
    """Verbatim text from the source, as it appears there."""

    source: str
    """Where the quote came from — a URL, or a path inside this container."""


class Verdict(StoreModel):
    """What an auditor concluded, recorded where a scorer can read it.

    The store rather than the completion: `react` strips the submit tool call from the
    message history by default, and a run that hits a token or cost limit can end with
    an empty completion — but the store survives both.
    """

    grade: str | None = Field(default=None)
    evidence: list[Evidence] = Field(default_factory=list)
    tried: str | None = Field(default=None)
    remarks: str | None = Field(default=None)


@tool
def submit_grade() -> Tool:
    async def execute(
        grade: str,
        evidence: list[Evidence],
        tried: str,
        remarks: str,
    ) -> str:
        """Submit your audit of this item.

        Args:
            grade: One of CORRECT, INCORRECT, ALTERNATIVES, UNVERIFIABLE.
            evidence: Verbatim quotes establishing the grade, each with its source.
                Quote what the source says; do not summarise what you concluded.
            tried: What you did to try to break the item, including what failed.
            remarks: What you actually think, including anything you were not asked about.
        """
        # ToolErrors are fed back to the model as recoverable errors, so a submission
        # that does not meet the contract becomes a retry rather than a lost audit.
        if grade not in GRADES:
            raise ToolError(f"grade must be one of {', '.join(GRADES)}, got {grade!r}")

        if grade != "UNVERIFIABLE" and not evidence:
            raise ToolError(
                f"a grade of {grade} needs at least one verbatim quote with its source. "
                "If no source you read establishes the answer, the grade is UNVERIFIABLE."
            )
        for item in evidence:
            if not item.quote.strip() or not item.source.strip():
                raise ToolError("every piece of evidence needs both a quote and its source")

        submitted = store_as(Verdict)
        submitted.grade = grade
        submitted.evidence = evidence
        submitted.tried = tried
        submitted.remarks = remarks
        return json.dumps({"grade": grade, "sources": [e.source for e in evidence]})

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
            explanation="\n\n".join(
                f"{e.quote}\n  -- {e.source}" for e in submitted.evidence
            )
            or None,
            metadata={
                "evidence": [e.model_dump() for e in submitted.evidence],
                "tried": submitted.tried,
                "remarks": submitted.remarks,
            },
        )

    return score
