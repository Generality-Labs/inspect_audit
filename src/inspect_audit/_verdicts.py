"""Investigation checks and their verdicts: skills as checks, `record_verdict`, `submit`, scorers.

A check is a skill whose frontmatter declares the grades it can return, which of
them need no evidence, the detail fields a verdict must carry and the benchmark
tools it may use. Verdicts are kept in the store and surfaced by one scorer per
check.
"""

import json
from pathlib import Path
from typing import Any

from inspect_ai.scorer import Score, Scorer, Target, frequency, scorer
from inspect_ai.solver import TaskState
from inspect_ai.tool import Tool, ToolDef, ToolError, ToolParam, read_skills, tool
from inspect_ai.util import StoreModel, sample_limits, store_as
from pydantic import BaseModel, Field, JsonValue

SKILLS = Path(__file__).parent / "skills"

# support skills help with the work rather than defining it; an investigator is never
# asked to audit one of these
SUPPORT_SKILLS = ("map-inspect-packages", "audit-framework")

# skills for reading .eval files in a box: the auditor's, never the investigator's (the
# investigator reads logs host-side), and never checks
LOG_SKILLS = ("reading-logs", "analyzing-logs")


class InvestigationItemSkill(BaseModel):
    """One investigation item, read from its skill's frontmatter."""

    name: str
    description: str
    grades: list[str]
    flags: list[str] = Field(default_factory=list)
    """Grades that mark a problem for the auditor to review."""
    unresolved: list[str] = Field(default_factory=list)
    """Grades that mean the check could not be settled. A descriptive check declares
    neither: its grades describe, they do not judge."""
    details: dict[str, str] = Field(default_factory=dict)
    """Detail fields this item requires with a verdict, as name -> description."""
    tools: list[str] = Field(default_factory=list)
    """Benchmark tools this check grants the investigator: `reset` (rebuild the
    benchmark's boxes). `grade` needs no grant: it is granted whenever the
    benchmark has a grader."""


# tools a check's frontmatter may grant. `grade` is listed for legibility only:
# it is granted whenever the benchmark has a grader.
_GRANTABLE_TOOLS = frozenset({"grade", "reset"})


def _item_skill(name: str, description: str, metadata: dict[str, Any]) -> InvestigationItemSkill:
    """Read one item skill's frontmatter contract, loudly.

    The frontmatter drives grade validation, evidence rules and tool grants, so
    a malformed block must fail here at load. Coerced to an empty contract it
    would instead surface at verdict time, as `record_verdict` rejecting every
    grade forever -- a paid run burned against its limits with no verdict.
    """

    def str_list(key: str) -> list[str]:
        value = metadata.get(key, [])
        if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
            raise ValueError(
                f"investigation skill {name!r}: frontmatter `{key}` must be a list of "
                f"strings, got {value!r}"
            )
        return value

    grades = str_list("grades")
    if not grades:
        raise ValueError(
            f"investigation skill {name!r} declares no grades: its frontmatter needs "
            "`metadata.grades`, the exhaustive list of verdicts it can produce"
        )
    flags, unresolved = str_list("flags"), str_list("unresolved")
    for key, named in (("flags", flags), ("unresolved", unresolved)):
        if rogue := set(named) - set(grades):
            raise ValueError(
                f"investigation skill {name!r}: `{key}` names grades it does not "
                f"declare: {', '.join(sorted(rogue))}"
            )
    if both := set(flags) & set(unresolved):
        raise ValueError(
            f"investigation skill {name!r}: {', '.join(sorted(both))} both flag and unresolve"
        )
    tools = str_list("tools")
    if unknown := set(tools) - _GRANTABLE_TOOLS:
        raise ValueError(
            f"investigation skill {name!r}: `tools` grants unknown tools "
            f"{', '.join(sorted(unknown))} "
            f"(grantable: {', '.join(sorted(_GRANTABLE_TOOLS))})"
        )
    declared = metadata.get("details", {})
    if not isinstance(declared, dict):
        raise ValueError(
            f"investigation skill {name!r}: frontmatter `details` must be a mapping of "
            f"field name to description, got {declared!r}"
        )
    return InvestigationItemSkill(
        name=name,
        description=description,
        grades=grades,
        flags=flags,
        unresolved=unresolved,
        details={str(k): str(v) for k, v in declared.items()},
        tools=tools,
    )


def effort_problem(model: object, reasoning_effort: object) -> str | None:
    """Why an investigator's reasoning effort cannot apply, if it cannot.

    There is nothing to bind it to without an explicit model (the eval's model is not
    resolved at task-construction time), and dropping the knob silently corrupts
    effort comparisons.
    """
    if reasoning_effort is not None and model is None:
        return (
            "reasoning_effort needs an explicit model to bind to: pass "
            "model= alongside it (the eval-level --model cannot carry it)"
        )
    return None


def investigation_items(items: list[str] | None = None) -> list[InvestigationItemSkill]:
    """The investigation items an investigator can audit, one skill each.

    Args:
        items: Restrict to these item names (defaults to all of them).

    Raises:
        ValueError: A skill's frontmatter contract is malformed, or `items`
            names an unknown item.
    """
    dirs = [
        path
        for path in sorted(SKILLS.iterdir())
        if path.is_dir() and path.name not in (*SUPPORT_SKILLS, *LOG_SKILLS)
    ]
    read = [
        _item_skill(s.name, s.description, s.metadata or {})
        for s in read_skills([str(d) for d in dirs])
    ]
    if items is not None:
        known = {s.name for s in read}
        unknown = set(items) - known
        if unknown:
            raise ValueError(
                f"Unknown investigation item(s) {', '.join(sorted(unknown))}. "
                f"Available: {', '.join(sorted(known))}."
            )
        read = [s for s in read if s.name in items]
    return read


class Evidence(BaseModel):
    """One observation and its provenance: a url, a log path, or a command run."""

    observed: str
    source: str


class Verdict(BaseModel):
    """An investigator's verdict on one investigation item."""

    grade: str
    explanation: str = ""
    approaches: str | None = None
    evidence: list[Evidence] = Field(default_factory=list)
    details: dict[str, JsonValue] = Field(default_factory=dict)
    tried: str | None = None
    remarks: str | None = None


class AssessmentUnits(StoreModel):
    """The assessment-unit ids question labels must cover for this sample."""

    ids: list[str] = Field(default_factory=list)


class Verdicts(StoreModel):
    """Recorded verdicts, keyed by investigation item name."""

    verdicts: dict[str, Verdict] = Field(default_factory=dict)
    debrief: dict[str, list[Evidence]] = Field(default_factory=dict)
    # why record_verdict refused each item, so a missing verdict can say why
    rejected: dict[str, list[str]] = Field(default_factory=dict)


def _details_help(skill: InvestigationItemSkill, sent: object) -> str:
    """What record_verdict needs in `details`, said so a model can correct itself."""
    fields = "; ".join(f"{key}: {description}" for key, description in skill.details.items())
    shape = json.dumps(dict.fromkeys(skill.details, "..."))
    began = str(sent)[:60].replace("\n", " ")
    return (
        f"details must be a JSON object as text, starting with '{{', but it began "
        f"{began!r}. Put your summary in remarks. For {skill.name} the object needs "
        f"{fields or 'no fields: send {}'}. Shape: {shape if skill.details else '{}'}"
    )


@tool
def record_verdict(items: list[InvestigationItemSkill]) -> Tool:
    lookup = {item.name: item for item in items}

    async def execute(
        item: str,
        grade: str,
        explanation: str,
        evidence: list[Evidence],
        approaches: str,
        tried: str,
        remarks: str,
        details: str,
    ) -> str:
        """Record your verdict on one investigation item.

        Args:
            item: The check name, such as gold-answer, not the sample ID.
            grade: Your grade for this item.
            explanation: Why this grade: the reasoning from the evidence to the grade,
                in a few sentences. An unresolved grade says what stopped you.
            evidence: Verbatim observations establishing the grade, each with its
                source. Record what you observed; do not summarise what you concluded.
            approaches: One sentence per attempt: how it approached the task, and
                whether that path was the intended one.
            tried: What you did to try to break the item, including what failed.
            remarks: What you actually think, including anything you were not asked about.
            details: JSON-encoded object containing the required fields listed for
                this item. Use "{}" when no fields are required.
        """
        # a ToolError is fed back to the model as recoverable, so a submission that
        # misses the contract becomes a retry rather than a lost verdict
        skill = lookup.get(item)
        if skill is None:
            raise ToolError(f"Unknown item {item!r}. Expected one of {', '.join(lookup)}.")
        # a string in the schema, not an object: OpenAI's validator (Azure, via
        # OpenRouter) refuses an object property without a type, and the fields
        # items declare are lists, strings and booleans. Python callers pass a dict
        try:
            recorded_details = json.loads(details) if isinstance(details, str) else details
        except ValueError:
            recorded_details = None
        if not isinstance(recorded_details, dict):
            # 09-29 chess run: an investigator wrote prose summaries here 54 times against
            # a bare "must encode a valid JSON object" and never recovered
            raise ToolError(_details_help(skill, details))
        if grade not in skill.grades:
            raise ToolError(f"Grade for {item} must be one of {', '.join(skill.grades)}.")
        if not explanation.strip():
            raise ToolError(
                "Every verdict needs an explanation: why the evidence gives this grade."
            )
        if not evidence:
            # an unresolved grade too: what was tried, observed and where, so a
            # reviewer can see the attempt and pick it up
            raise ToolError(f"A grade of {grade} needs at least one observation with its source.")
        for entry in evidence:
            if not entry.observed.strip() or not entry.source.strip():
                raise ToolError("Every piece of evidence needs both an observation and its source.")
        missing = [key for key in skill.details if key not in recorded_details]
        if missing:
            asks = ", ".join(f"{key} ({skill.details[key]})" for key in missing)
            raise ToolError(f"This item also requires details: {asks}.")

        if item == "question-labels":
            from ._coverage import validate_labels

            expected = store_as(AssessmentUnits).ids
            if not expected:
                raise ToolError("Question labels need the sample's assessment ids; none were set")
            try:
                labels = validate_labels(
                    recorded_details["question_assessments"], expected, require_classification=True
                )
                overall = (
                    "DEFECT"
                    if any(r.status == "DEFECT" for r in labels)
                    else "UNRESOLVED"
                    if any(r.status in ("UNRESOLVED", "NOT_ASSESSED") for r in labels)
                    else "NO_ISSUE_FOUND"
                )
                if grade != overall:
                    raise ValueError(f"Overall grade must be {overall} for these question labels")
            except ValueError as ex:
                raise ToolError(str(ex)) from ex

        # replace rather than mutate so the store sees the change
        recorded = store_as(Verdicts)
        recorded.verdicts = {
            **recorded.verdicts,
            item: Verdict(
                grade=grade,
                explanation=explanation,
                approaches=approaches,
                evidence=evidence,
                details=recorded_details,
                tried=tried,
                remarks=remarks,
            ),
        }
        return json.dumps({"item": item, "grade": grade})

    async def recording(
        item: str,
        grade: str,
        explanation: str,
        evidence: list[Evidence],
        approaches: str,
        tried: str,
        remarks: str,
        details: str,
    ) -> str:
        try:
            return await execute(
                item, grade, explanation, evidence, approaches, tried, remarks, details
            )
        except ToolError as ex:
            store_as(Verdicts).rejected.setdefault(item, []).append(str(ex))
            raise

    recording.__doc__ = execute.__doc__
    definition = ToolDef(recording, name="record_verdict")
    definition.parameters.properties["item"] = ToolParam(
        type="string",
        enum=list(lookup),
        description="The check name, not the sample ID.",
    )
    fields: dict[str, list[str]] = {}
    for item in items:
        for key, description in item.details.items():
            fields.setdefault(key, []).append(f"{item.name}: {description}")
    definition.parameters.properties["details"] = ToolParam(
        type="string",
        description="A JSON object as text, starting with '{' (not prose: your summary "
        "goes in remarks). Required fields by item:\n"
        + "\n".join(f"{item.name}: {', '.join(item.details) or '(none)'}" for item in items),
    )
    # Keep arbitrary nested skill data inside the JSON string and validate it above.
    details = definition.parameters.properties["details"]
    details.description = (
        (details.description or "")
        + "\n"
        + "\n".join(f"{key}: {'; '.join(descriptions)}" for key, descriptions in fields.items())
    )
    return definition.as_tool()


@tool
def submit_investigation(items: list[InvestigationItemSkill]) -> Tool:
    async def execute(
        environment_issues: list[Evidence],
        unresolved: list[Evidence],
        improvements: list[Evidence],
    ) -> str:
        """Submit your investigation, once every item has a recorded verdict.

        Args:
            environment_issues: Audit setup or tool failures, with tool-event sources.
            unresolved: Blocked or unreviewed checks and their evidence references.
            improvements: Suggested changes with supporting observations; distinguish
                investigation repairs from benchmark changes and state whether the intended
                task is preserved. These are proposals, not benchmark findings.
        """
        recorded = store_as(Verdicts).verdicts
        missing = [item.name for item in items if item.name not in recorded]
        if missing:
            raise ToolError(f"No verdict recorded for: {', '.join(missing)}.")
        store_as(Verdicts).debrief = {
            "environment_issues": environment_issues or [],
            "unresolved": unresolved or [],
            "improvements": improvements or [],
        }
        return json.dumps({item: verdict.grade for item, verdict in recorded.items()})

    return execute


def _no_verdict_reason(rejected: list[str]) -> str:
    """Why an item has no verdict, from its refusals and the sample's limits.

    09-29: two items came back NO_VERDICT with nothing but concordance metadata, and
    the auditor blamed the wrong thing.
    """
    reasons = [
        f"record_verdict refused it {len(rejected)} time(s); last: {rejected[-1]}"
        if rejected
        else "the investigator never called record_verdict for this item"
    ]
    try:
        limits = sample_limits()
    except RuntimeError:  # scored outside a running sample
        limits = None
    if limits is not None:
        for name in ("cost", "token", "time", "working", "message", "turn"):
            limit = getattr(limits, name)
            if limit.limit is not None and limit.usage >= limit.limit:
                reasons.append(
                    f"the sample hit its {name} limit ({limit.usage:g} of {limit.limit:g})"
                )
    return "; ".join(reasons)


def item_scorer(item: InvestigationItemSkill) -> Scorer:
    """A scorer surfacing the investigator's verdict on one investigation item."""
    # inspect's own categorical metric, with the skill's grades declared so a
    # grade nobody earned still reports as 0.0 and the metric round-trips
    # through recompute_metrics(). a dynamic registry name gives each item its
    # own score column; the cost is that a cold `inspect score` cannot resolve
    # these names to re-score a log
    distribution = frequency(categories=[*item.grades, "NO_VERDICT"])

    @scorer(metrics=[distribution], name=item.name)
    def factory() -> Scorer:
        async def score(state: TaskState, target: Target) -> Score:
            verdict = state.store_as(Verdicts).verdicts.get(item.name)
            if verdict is None:
                rejected = state.store_as(Verdicts).rejected.get(item.name, [])
                return Score(
                    value="NO_VERDICT",
                    explanation=_no_verdict_reason(rejected),
                    metadata={"rejections": len(rejected)},
                )
            return Score(
                value=verdict.grade,
                answer=verdict.grade,
                explanation=verdict.explanation or None,
                metadata={
                    "flagged": verdict.grade in item.flags,
                    "unresolved": verdict.grade in item.unresolved,
                    "evidence": [e.model_dump() for e in verdict.evidence],
                    "approaches": verdict.approaches,
                    **verdict.details,
                    "tried": verdict.tried,
                    "remarks": verdict.remarks,
                    "debrief": {
                        key: [e.model_dump() for e in entries]
                        for key, entries in state.store_as(Verdicts).debrief.items()
                    },
                },
            )

        return score

    return factory()
