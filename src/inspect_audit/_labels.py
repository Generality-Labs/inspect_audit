"""Validation of per-question labels recorded by the question-labels check."""

from collections import Counter
from collections.abc import Sequence
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

# Epoch's per-item defect classes (Rubric v1, the scoring row's examples), in Epoch's
# order; the fourth, a task that egregiously does not measure the claimed capability,
# is a rubric-level judgment and never an item's label. Each defect names one class
# and one sub-reason from that class's list; `other` keeps an unlisted cause sayable,
# with the explanation naming it.
EPOCH_CLASSES = ("impossible_as_written", "false_negative", "false_positive")
SUB_REASONS: dict[str, tuple[str, ...]] = {
    "impossible_as_written": (
        "ambiguous_wording",
        "conflicting_sources",
        "multiple_valid_answers",
        "missing_information",
        "hidden_requirement",
        "false_premise",
        "other",
    ),
    "false_negative": (
        "incorrect_or_stale_key",
        "overly_strict_scorer",
        "systematic_grader_error",
        "live_state_drift",
        "environment_failure",
        "other",
    ),
    "false_positive": ("lax_scorer", "systematic_grader_error", "shortcut_or_leak", "other"),
}
# a recorded grade is wrong one way or the other; whether the item can be answered as
# written is the question-labels check's call
GRADING_CLASSES = ("false_negative", "false_positive")
CLASS_TITLES = {
    "impossible_as_written": "Essentially impossible to correctly answer as written",
    "false_negative": "False negative",
    "false_positive": "False positive",
}

# Findings recorded before classes were: each old defect type read as a class and
# sub-reason. A row's first type that maps decides it; `other` maps to nothing.
LEGACY_CLASSES: dict[str, tuple[str, str]] = {
    "missing_information": ("impossible_as_written", "missing_information"),
    "hidden_requirement": ("impossible_as_written", "hidden_requirement"),
    "specification_test_contradiction": ("false_negative", "incorrect_or_stale_key"),
    "incorrect_reference_or_test": ("false_negative", "incorrect_or_stale_key"),
    "surface_form_sensitivity": ("false_negative", "overly_strict_scorer"),
    "grader_context_omission": ("false_negative", "systematic_grader_error"),
    "environment_failure": ("false_negative", "environment_failure"),
    "harness_failure": ("false_negative", "environment_failure"),
    "insufficient_test_coverage": ("false_positive", "lax_scorer"),
    # grading-labels
    "false_negative": ("false_negative", "overly_strict_scorer"),
    "grader_failure": ("false_negative", "systematic_grader_error"),
    "false_positive": ("false_positive", "lax_scorer"),
}


def legacy_class(defect_types: Sequence[str]) -> tuple[str, str] | None:
    """The class and sub-reason an old row's defect types stand for, if any maps."""
    return next((LEGACY_CLASSES[t] for t in defect_types if t in LEGACY_CLASSES), None)


def check_class(
    name: str,
    epoch_class: str | None,
    sub_reason: str | None,
    classes: Sequence[str] = EPOCH_CLASSES,
) -> None:
    """A class from `classes` and a sub-reason from that class's list, or neither."""
    if epoch_class is None and sub_reason is None:
        return
    if epoch_class is None or epoch_class not in classes:
        raise ValueError(f"{name}: epoch_class must be one of {', '.join(classes)}")
    allowed = SUB_REASONS[epoch_class]
    if sub_reason not in allowed:
        raise ValueError(
            f"{name}: sub_reason for {epoch_class} must be one of {', '.join(allowed)}"
        )


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


# what a tie between investigators stands as until the auditor rules, on the label
# checks: a defect, so the cell is flagged for review rather than settled by whichever
# verdict arrived last
TIE = "DEFECT"


def majority_label(votes: list[tuple[str, str]], flags: Sequence[str] = (TIE,)) -> str | None:
    """The grade most investigators gave a cell, before any auditor ruling.

    `votes` are (investigator, label) in the order recorded; each investigator (one
    job) counts once, by its latest label, and NO_VERDICT does not vote. A tie stands
    as a flagged grade, so the auditor sees it: the latest tied label that is one of
    `flags`, else the first of `flags`, else (a check that flags nothing) the latest
    tied label. None where fewer than two investigators recorded anything, or none
    voted: the cell's latest take stands, as it always has.
    """
    latest: dict[str, str] = {}
    for voter, label in votes:
        latest[voter] = label
    if len(latest) < 2:
        return None
    counted = Counter(label for label in latest.values() if label != "NO_VERDICT")
    if not counted:
        return None
    top = max(counted.values())
    tied = [label for label in latest.values() if counted.get(label) == top]
    if len(set(tied)) == 1:
        return tied[0]
    flagged = [label for label in tied if label in flags]
    return flagged[-1] if flagged else (flags[0] if flags else tied[-1])


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
    # Empty on historical logs; kept, optional, beside the class below.
    defect_types: list[str] = Field(default_factory=list)
    # Epoch's class for a DEFECT and its sub-reason (`SUB_REASONS`); required on new
    # defect verdicts, absent on historical ones, which `legacy_class` reads instead
    epoch_class: str | None = None
    sub_reason: str | None = None
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
    classes = GRADING_CLASSES if grading else EPOCH_CLASSES
    for row in rows:
        if row.status in ("NO_ISSUE_FOUND", "DEFECT") and (not row.checks or not row.evidence):
            raise ValueError(f"{row.question_id}: assessed labels need checks and evidence")
        if (
            require_classification
            and row.status == "DEFECT"
            and not (row.epoch_class and row.sub_reason)
        ):
            raise ValueError(
                f"{row.question_id}: defects need epoch_class ({', '.join(classes)}) and a "
                "sub_reason from that class's list"
            )
        if (row.epoch_class or row.sub_reason) and row.status != "DEFECT":
            raise ValueError(f"{row.question_id}: only established defects have a class")
        check_class(row.question_id, row.epoch_class, row.sub_reason, classes)
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
