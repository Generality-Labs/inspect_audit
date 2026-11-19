"""Validation of per-question labels recorded by the question-labels check."""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

# The names a new defect verdict may use. Free text gave 27 spellings of about ten
# mechanisms on CL-bench (FRICTION 28); `other` keeps an unlisted cause sayable,
# with the explanation naming it.
DEFECT_TYPES = (
    "missing_information",
    "specification_test_contradiction",
    "incorrect_reference_or_test",
    "insufficient_test_coverage",
    "hidden_requirement",
    "surface_form_sensitivity",
    "grader_context_omission",
    "environment_failure",
    "harness_failure",
    "other",
)

# what a recorded grade can get wrong: the grading-labels check's defect types, which
# label the grades given to an item's recorded submissions, not the item
GRADING_DEFECT_TYPES = ("false_negative", "false_positive", "grader_failure")


class Source(BaseModel):
    """One competing answer and where it is published, for the page's reader to check.

    A defect that rests on conflicting sources, or on a second defensible answer, lists
    every competing answer this way, the key's included: the page prints each with its
    source, its link and the passage that makes the claim.
    """

    model_config = ConfigDict(extra="forbid")
    answer: str = Field(min_length=1)
    # the work and who published it: "Discogs release 7082924", "UK.PROJECT label page"
    source: str = Field(min_length=1)
    # the public URL, or an exact citation (volume, page, document number) for print
    url: str = Field(min_length=1)
    # the passage, quoted, that gives this answer
    quote: str = Field(min_length=1)
    # whether this is the answer the benchmark's reference takes
    key: bool = False


# a quoted claim is the passage that gives the answer, not the page around it
QUOTE_WORDS = 60


def check_sources(name: str, sources: list[Source]) -> None:
    """Competing answers: at least two, each cited where a reader can find it."""
    if not sources:
        return
    answers = {" ".join(s.answer.lower().split()) for s in sources}
    if len(answers) < 2:
        raise ValueError(
            f"{name}: sources list competing answers, at least two different ones (the "
            "key's and the one it rejects); one answer is evidence, not a conflict"
        )
    for i, source in enumerate(sources, 1):
        if any(not v.strip() for v in (source.answer, source.source, source.url, source.quote)):
            raise ValueError(f"{name}: source {i} needs answer, source, url and quote")
        if len(source.quote.split()) > QUOTE_WORDS:
            raise ValueError(
                f"{name}: source {i} quotes {len(source.quote.split())} words; quote the "
                f"passage that gives the answer, at most {QUOTE_WORDS}"
            )
        if source.url.strip().startswith(("/", "~", "file:")):
            raise ValueError(
                f"{name}: source {i} cites a local copy ({source.url}); give the public URL "
                "the copy was fetched from, or an exact citation"
            )


class QuestionAssessment(BaseModel):
    """One question's validity, independent of a model's recorded pass or failure."""

    model_config = ConfigDict(extra="forbid")
    question_id: str = Field(min_length=1)
    status: Literal["NO_ISSUE_FOUND", "DEFECT", "UNRESOLVED", "NOT_ASSESSED"]
    checks: list[str]
    evidence: list[str]
    explanation: str = Field(min_length=1)
    # Empty on historical logs; new defect verdicts require a classification.
    defect_types: list[str] = Field(default_factory=list)
    scoring_effect: str | None = None
    next_check: str | None = None
    # The grader scoring the same answer differently: about the grader, not this
    # question, so it never makes the question a DEFECT (FRICTION 29).
    grader_instability: str | None = None
    # one plain sentence for the page's reader saying what is wrong, required on a
    # grading-labels defect, where the page prints it beside the item
    headline: str | None = None
    # the competing answers a defect rests on, each with its source; see `Source`
    sources: list[Source] = Field(default_factory=list)


def validate_labels(
    value: Any,
    expected: list[str],
    *,
    require_classification: bool = False,
    allowed: tuple[str, ...] = DEFECT_TYPES,
) -> list[QuestionAssessment]:
    """Require each expected question once; a clean verdict needs completed checks.

    `allowed` is the check's own defect vocabulary: the item's mechanisms for
    question-labels, what a grade can get wrong for grading-labels, whose defects
    also carry a headline for the page.
    """
    rows = TypeAdapter(list[QuestionAssessment]).validate_python(value)
    ids = [r.question_id for r in rows]
    if len(ids) != len(set(ids)) or set(ids) != set(expected):
        raise ValueError("question_assessments must contain every expected question exactly once")
    grading = allowed is GRADING_DEFECT_TYPES
    for row in rows:
        if row.status in ("NO_ISSUE_FOUND", "DEFECT") and (not row.checks or not row.evidence):
            raise ValueError(f"{row.question_id}: assessed labels need checks and evidence")
        if require_classification and row.status == "DEFECT" and not row.defect_types:
            raise ValueError(f"{row.question_id}: defects need at least one defect type")
        unknown = set(row.defect_types) - set(allowed)
        if require_classification and unknown:
            raise ValueError(
                f"{row.question_id}: unknown defect type(s) {sorted(unknown)}; use "
                f"{', '.join(allowed)}"
                + ("" if grading else " (grader instability goes in grader_instability)")
            )
        if grading and row.status == "DEFECT":
            if not (row.headline or "").strip():
                raise ValueError(f"{row.question_id}: a grading defect needs a headline")
            if len(row.headline.split()) > 25:  # type: ignore[union-attr]
                raise ValueError(f"{row.question_id}: headline over 25 words")
        if row.defect_types and row.status != "DEFECT":
            raise ValueError(f"{row.question_id}: only established defects have defect types")
        if len(row.defect_types) != len(set(row.defect_types)):
            raise ValueError(f"{row.question_id}: duplicate defect types")
        if any(not s.strip() for s in row.checks + row.evidence + row.defect_types):
            raise ValueError(f"{row.question_id}: empty check or evidence reference")
        check_sources(row.question_id, row.sources)
    return rows
