import json
from copy import copy
from pathlib import Path
from typing import Any

from inspect_ai.agent import Agent, AgentSubmit, agent, react
from inspect_ai.model import GenerateConfig, Model, get_model
from inspect_ai.scorer import (
    Metric,
    SampleScore,
    Score,
    Scorer,
    Target,
    Value,
    metric,
    scorer,
)
from inspect_ai.solver import TaskState
from inspect_ai.solver._task_state import sample_state
from inspect_ai.tool import Tool, ToolDef, ToolError, bash, skill, tool
from inspect_ai.tool._tools._skill import read_skills
from inspect_ai.util import StoreModel, sandbox_default, store_as
from pydantic import BaseModel, Field, JsonValue

from ._item import AUDIT_ROOT
from ._sandbox import BENCHMARK_SERVICE, restore_benchmark

SKILLS = Path(__file__).parent / "skills"

# support skills help with the work rather than defining it; an auditor is never
# asked to investigate one of these
SUPPORT_SKILLS = ("reading-logs", "analyzing-logs", "map-inspect-packages")

AUDIT_PROMPT = f"""\
You are auditing one item from an AI benchmark: one question, its recorded answer, and
every recorded attempt at it by many models.

  {AUDIT_ROOT}/sample.json       the item as the benchmark defines it
  {AUDIT_ROOT}/logs/*.eval       real Inspect logs: each attempt, its answer, its
                           grade, the judge's explanation, the full transcript
  {AUDIT_ROOT}/gold/grading.md   where the grading code lives
  {AUDIT_ROOT}/env/              how this container was built

The benchmark's own code is installed here, so read the real source in place. You have
a shell in this container, with curl and the internet.

Every benchmark question tests work: something the agent must do to earn the answer.
Establish, for each question, what that work is. An answer that arrives without the
tested work got around it somehow -- the items you are investigating are specific
routes around the work. Attribute any unearned answer to its route, with evidence.

You are investigating:

{{items}}

Invoke each skill and follow it. Other skills are available for working with the logs.
Record a verdict for every item with the evidence that earned it, and say what you
actually think. Then submit.
{{notes}}"""

# rendered into the prompt only when the operator sets `notes`: a free-form steer
# ("what the operator has been thinking about"), kept separate from the skills so a
# skill stays general and the steer stays a per-run knob
NOTES_SECTION = """

## Notes from the operator

{notes}
"""


class AuditItemSkill(BaseModel):
    """One audit item, read from its skill's frontmatter."""

    name: str
    description: str
    grades: list[str]
    unevidenced: list[str] = Field(default_factory=list)
    details: dict[str, str] = Field(default_factory=dict)
    """Detail fields this item requires with a verdict, as name -> description."""
    tools: list[str] = Field(default_factory=list)
    """Benchmark tools this item grants the auditor, e.g. `grade`, `reset`."""


def audit_items(items: list[str] | None = None) -> list[AuditItemSkill]:
    """The audit items an auditor can investigate, one skill each.

    Args:
        items: Restrict to these item names (defaults to all of them).
    """
    dirs = [
        path
        for path in sorted(SKILLS.iterdir())
        if path.is_dir() and path.name not in SUPPORT_SKILLS
    ]
    read = []
    for s in read_skills([str(d) for d in dirs]):
        metadata = s.metadata or {}

        def names(key: str, metadata: dict[str, Any] = metadata) -> list[str]:
            value = metadata.get(key, [])
            return [str(g) for g in value] if isinstance(value, list) else []

        declared = metadata.get("details", {})
        read.append(
            AuditItemSkill(
                name=s.name,
                description=s.description,
                grades=names("grades"),
                unevidenced=names("unevidenced"),
                details={str(k): str(v) for k, v in declared.items()}
                if isinstance(declared, dict)
                else {},
                tools=names("tools"),
            )
        )
    if items is not None:
        known = {s.name for s in read}
        unknown = set(items) - known
        if unknown:
            raise ValueError(
                f"Unknown audit item(s) {', '.join(sorted(unknown))}. "
                f"Available: {', '.join(sorted(known))}."
            )
        read = [s for s in read if s.name in items]
    return read


class Evidence(BaseModel):
    """One observation and its provenance: a url, a log path, or a command run."""

    observed: str
    source: str


class Verdict(BaseModel):
    """An auditor's verdict on one audit item."""

    grade: str
    approaches: str | None = None
    evidence: list[Evidence] = Field(default_factory=list)
    details: dict[str, JsonValue] = Field(default_factory=dict)
    tried: str | None = None
    remarks: str | None = None


class Verdicts(StoreModel):
    """Recorded verdicts, keyed by audit item name."""

    verdicts: dict[str, Verdict] = Field(default_factory=dict)


@tool
def record_verdict(items: list[AuditItemSkill]) -> Tool:
    lookup = {item.name: item for item in items}

    async def execute(
        item: str,
        evidence: list[Evidence],
        approaches: str,
        tried: str,
        remarks: str,
        grade: str,
        details: str,
    ) -> str:
        """Record your verdict on one audit item.

        Args:
            item: The audit item this verdict is for.
            evidence: Verbatim observations establishing the grade, each with its
                source. Record what you observed; do not summarise what you concluded.
            approaches: One sentence per attempt: how it approached the task, and
                whether that path was the intended one.
            tried: What you did to try to break the item, including what failed.
            remarks: What you actually think, including anything you were not asked about.
            grade: Your grade for this item.
            details: JSON object holding any further fields this item's skill
                asks you to record ("{}" when it asks for none).
        """
        # a ToolError is fed back to the model as recoverable, so a submission that
        # misses the contract becomes a retry rather than a lost verdict
        skill = lookup.get(item)
        if skill is None:
            raise ToolError(f"Unknown item {item!r}. Expected one of {', '.join(lookup)}.")
        try:
            recorded_details: dict[str, JsonValue] = json.loads(details)
            if not isinstance(recorded_details, dict):
                raise ValueError
        except ValueError:
            raise ToolError("details must be a JSON object.") from None
        if grade not in skill.grades:
            raise ToolError(f"Grade for {item} must be one of {', '.join(skill.grades)}.")
        if not evidence and grade not in skill.unevidenced:
            raise ToolError(f"A grade of {grade} needs at least one observation with its source.")
        for entry in evidence:
            if not entry.observed.strip() or not entry.source.strip():
                raise ToolError(
                    "Every piece of evidence needs both an observation and its source."
                )
        missing = [key for key in skill.details if key not in recorded_details]
        if missing:
            asks = ", ".join(f"{key} ({skill.details[key]})" for key in missing)
            raise ToolError(f"This item also requires details: {asks}.")

        # replace rather than mutate so the store sees the change
        recorded = store_as(Verdicts)
        recorded.verdicts = {
            **recorded.verdicts,
            item: Verdict(
                grade=grade,
                approaches=approaches,
                evidence=evidence,
                details=recorded_details,
                tried=tried,
                remarks=remarks,
            ),
        }
        return json.dumps({"item": item, "grade": grade})

    return execute


@tool
def grade_benchmark(scorers: list[Scorer]) -> Tool:
    async def execute(answer: str) -> str:
        """Grade the benchmark environment with the benchmark's own grader.

        Runs the real scorer against the benchmark service as it currently stands,
        and returns its grade and explanation. Apply a candidate answer inside
        `benchmark_bash` first (or pass one as `answer`), then grade to see whether
        the benchmark accepts it -- turning "an answer is reachable" into "the
        grader credits it". Anything you leave in the box persists until you reset.

        Args:
            answer: Submission to grade as the attempt's answer. Pass an empty
                string to grade the benchmark environment exactly as it stands.
        """
        if not scorers:
            raise ToolError("This benchmark exposes no grader to grade with.")
        state = sample_state()
        if state is None:
            raise ToolError("Grading is only available while auditing a sample.")

        graded = copy(state)
        if answer:
            graded.output = copy(state.output)
            graded.output.completion = answer

        # present the benchmark's own sample to its grader: its metadata (base_commit,
        # target files, whatever the scorer reads) rather than the audit sample's
        graded.metadata = {
            **(state.metadata or {}),
            **((state.metadata or {}).get("benchmark_metadata") or {}),
        }

        # the benchmark's scorer calls sandbox() expecting the eval's own box; in the
        # auditor's two-box world that default is us, so aim it at the benchmark
        results: list[JsonValue] = []
        with sandbox_default(BENCHMARK_SERVICE):
            for scorer in scorers:
                score = await scorer(graded, state.target)
                if score is not None:
                    results.append(
                        {
                            "value": score.value,
                            "answer": score.answer,
                            "explanation": score.explanation,
                        }
                    )
        return json.dumps(results if len(results) != 1 else results[0])

    return execute


@tool
def reset_benchmark() -> Tool:
    async def execute() -> str:
        """Restore the benchmark environment to its pristine per-sample state.

        Re-runs the sample's setup, undoing anything written to the box since.
        Use it between graded attempts so each starts from the same state.
        """
        state = sample_state()
        if state is None:
            raise ToolError("Reset is only available while auditing a sample.")
        await restore_benchmark((state.metadata or {}).get("benchmark_setup"))
        return "benchmark environment reset to its per-sample state"

    return execute


@tool
def submit_audit(items: list[AuditItemSkill]) -> Tool:
    async def execute() -> str:
        """Submit your audit, once every item has a recorded verdict."""
        recorded = store_as(Verdicts).verdicts
        missing = [item.name for item in items if item.name not in recorded]
        if missing:
            raise ToolError(f"No verdict recorded for: {', '.join(missing)}.")
        return json.dumps({item: verdict.grade for item, verdict in recorded.items()})

    return execute


@agent
def audit_agent(
    items: list[str] | None = None,
    model: str | None = None,
    benchmark_scorers: Scorer | list[Scorer] | None = None,
    reasoning_effort: str | None = None,
    notes: str | None = None,
) -> Agent:
    """An auditor: a react loop with the audit skills and a shell in the item's sandbox.

    Args:
        items: Audit items to investigate (defaults to all of them).
        model: Model to audit with (defaults to the evaluated model).
        benchmark_scorers: The audited task's own scorer(s), for the `grade` tool.
        reasoning_effort: Reasoning effort for the auditor model, when it takes one.
        notes: A free-form steer inserted into the system prompt -- what the operator
            has been thinking about (a suspected route, a specific hint). Kept out of
            the skills so a skill stays general and the steer stays a per-run knob.
    """
    # resolve the model object here so a generate config binds to it -- react
    # re-resolves a bare string without one, so the config would be dropped
    resolved: str | Model | None = model
    if model is not None and reasoning_effort is not None:
        resolved = get_model(model, config=GenerateConfig(reasoning_effort=reasoning_effort))
    # name the items under investigation in the system message: an auditor that is
    # not told what it is looking for picks whichever skill looks most relevant
    scoped = audit_items(items)
    named = "\n".join(f"- `{item.name}`: {item.description}" for item in scoped)

    skills = [str(path) for path in sorted(SKILLS.iterdir()) if path.is_dir()]

    tools = [
        bash(timeout=180),
        ToolDef(
            bash(timeout=180, sandbox=BENCHMARK_SERVICE),
            name="benchmark_bash",
            description=(
                "Run a command inside the environment the benchmark itself ran in, "
                "exactly as the evaluated agent saw it. Anything you download, write "
                "or install here is evidence about you, not about the environment."
            ),
        ).as_tool(),
        skill(skills),
        record_verdict(scoped),
    ]

    # benchmark tools are granted only when a scoped item's skill asks for them,
    # so a passive item keeps the box observe-only
    granted = {name for item in scoped for name in item.tools}
    if "grade" in granted:
        scorer_list = (
            benchmark_scorers
            if isinstance(benchmark_scorers, list)
            else [benchmark_scorers]
            if benchmark_scorers is not None
            else []
        )
        tools.append(grade_benchmark(scorer_list))
    if "reset" in granted:
        tools.append(reset_benchmark())

    return react(
        name="auditor",
        description="Audits one benchmark item and submits a verdict per audit item.",
        prompt=AUDIT_PROMPT.format(
            items=named,
            notes=NOTES_SECTION.format(notes=notes) if notes else "",
        ),
        tools=tools,
        model=resolved,
        submit=AgentSubmit(
            tool=submit_audit(scoped), name="submit", keep_in_messages=True
        ),
    )


@metric
def grades() -> Metric:
    """Proportion of items in each grade."""

    def compute(scores: list[SampleScore]) -> Value:
        counts: dict[str, int] = {}
        for score in scores:
            counts[str(score.score.value)] = counts.get(str(score.score.value), 0) + 1
        return {grade: count / len(scores) for grade, count in sorted(counts.items())}

    return compute


def item_scorer(item: str) -> Scorer:
    """A scorer surfacing the auditor's verdict on one audit item."""

    # a dynamic registry name so each item gets its own score column; the cost is
    # that a cold `inspect score` cannot resolve these names to re-score a log
    @scorer(metrics=[grades()], name=item)
    def factory() -> Scorer:
        async def score(state: TaskState, target: Target) -> Score:
            verdict = state.store_as(Verdicts).verdicts.get(item)
            if verdict is None:
                return Score(value="NO_VERDICT")
            return Score(
                value=verdict.grade,
                answer=verdict.grade,
                explanation="\n\n".join(
                    f"{e.observed}\n  -- {e.source}" for e in verdict.evidence
                )
                or None,
                metadata={
                    "evidence": [e.model_dump() for e in verdict.evidence],
                    "approaches": verdict.approaches,
                    **verdict.details,
                    "tried": verdict.tried,
                    "remarks": verdict.remarks,
                },
            )

        return score

    return factory()
