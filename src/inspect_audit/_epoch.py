"""The Epoch AI benchmark review as a report format.

Epoch's methodology is a decision procedure, not a parallel rating of dimensions: a
reviewability gate (stop at Not Enough Information), four minimum-standard defect
classes any one of which can make the benchmark Flawed, then seven evaluation-quality
questions that are desirable but not disqualifying. The agent records each row and a
short narrative in `report/review.json`; the verdict is derived here, never chosen by
the model, and the scoring row's prevalence is computed from the question-label
coverage the per-item auditors produce. `epoch_review.md` is rendered from those
records, the way the GL path renders `assessments.tex` from `assessments.json`.

The rendered review is written for people with a scientific background who are not
specialists in evaluations: a header block, a short summary, a few bounded sections
whose set depends on the verdict, and the rubric tables with brief notes. Word and
sentence budgets are enforced here so the length cannot drift with the model.
"""

import json
import re
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, model_validator

from ._report import EvidenceRef

FORMAT = "epoch"

Reviewability = Literal["Full", "Partial", "Inadequate", "Not Reviewed"]
MinimumStatus = Literal["Pass", "Flag", "Not Reviewed"]

MINIMUM_STANDARD: dict[str, str] = {
    "scoring": "Scoring",
    "consistency": "Benchmark consistency",
    "elicitation": "Elicitation",
    "bias": "Bias in evaluation setup",
}

# every quality question, its display name, and the statuses Epoch's form offers
QUALITY: dict[str, tuple[str, tuple[str, ...]]] = {
    "resource_adequacy": (
        "Elicitation and resource adequacy",
        ("Sufficient", "Constraining", "Unreasonably constraining", "Unknown", "Not Reviewed"),
    ),
    "scaffold_fairness": (
        "Scaffold fairness",
        (
            "Shared common scaffold",
            "Mix of model-specific and common scaffolds",
            "Model-specific scaffolds",
            "Not Reviewed",
        ),
    ),
    "contamination": ("Contamination", ("Assessed", "Not Reviewed")),
    "human_completability": (
        "Human completability",
        (
            "All tasks",
            "Representative set of tasks",
            "Poor implementation",
            "Not established",
            "Not Reviewed",
        ),
    ),
    "score_range": ("Score range", ("Estimated", "Not Reviewed")),
    "statistical_adequacy": ("Statistical adequacy", ("Known", "Unknown", "Not Reviewed")),
    "construct_validity": (
        "Construct validity",
        (
            "Measures stated capabilities",
            "Partially measures stated capabilities",
            "Does not measure stated capabilities",
            "Not Reviewed",
        ),
    ),
}

# the quantities a quality row carries beside its status, where the form has blanks
QUALITY_FIELDS: dict[str, tuple[str, ...]] = {
    "contamination": ("as_of", "tasks_public_pct", "solutions_public_pct"),
    "score_range": ("floor", "ceiling"),
    "statistical_adequacy": ("runs_per_model",),
}

# the fixed text of Epoch's rubric form, rendered on every review so the tables read
# exactly as the form does; only Status and Notes vary per review
REVIEWABILITY_LEVELS: dict[str, tuple[str, str]] = {
    "Full": (
        "All tasks and scoring logic inspectable, and harness/API settings used for each "
        "model (reasoning effort, token/time limits, tool access, system prompts) are fully "
        "disclosed",
        "[proceed to 2]",
    ),
    "Partial": (
        "A representative sample of tasks and scoring logic inspectable, with full "
        "harness/API settings disclosed",
        "[proceed to 2]",
    ),
    "Inadequate": (
        "Limited, biased, or no inspectable tasks or scoring logic, harness/API settings "
        "undisclosed",
        "[stop → NEI]",
    ),
}
MINIMUM_STANDARD_FORM: dict[str, tuple[list[str], str]] = {
    "scoring": (
        [
            "Essentially impossible to correctly answer as written (e.g. underspecified "
            "task, hidden requirement, missing file/tool)",
            "False Negative (e.g. overly strict scorer, stale/incorrect ground truth, "
            "dependent on live external state that can drift, sandbox failure independent "
            "of the agent)",
            "False Positive (e.g. lax scorer, reward-hackable environment, stated skill can "
            "be bypassed via a shortcut such as exploiting an error in the scoring logic or "
            "retrieving the answer from the harness/web)",
            "Task egregiously doesn't measure the claimed capability",
        ],
        "≥20% of inspected sample contains errors or issue that corrupts grading at scale",
    ),
    "consistency": (
        ["Scorer, instructions, or ground truth changed without a version bump"],
        "Leaderboard has incomparable results from different versions",
    ),
    "elicitation": (
        [
            "Model elicitation is extremely constraining and is not the focus of the benchmark:",
            "Under-resourced relative to task size (token/turn/time limit, sandbox resources)",
            "Poor context management",
            "Lack of agentic environment where one would be natural to provide",
            "Excessive non-voluntary termination for agentic benchmarks",
        ],
        "Substantially reduced performance compared to reasonable alternatives for the tasks",
    ),
    "bias": (
        [
            "Uneven compute/token budgets",
            "Unfair scaffold choice (e.g. only a subset of models optimized)",
        ],
        "Material model-specific advantage found",
    ),
}
MINIMUM_STATUS_FORM = ("Pass", "Flag [stop → Flawed]", "Not Reviewed")
QUALITY_QUESTIONS: dict[str, str] = {
    "resource_adequacy": (
        "Elicitation and resource adequacy: Are the resources given to models (reasoning "
        "token/turn budget, tool access, etc.) sufficient for them to perform near their "
        "ceiling?"
    ),
    "scaffold_fairness": "Scaffold fairness: What scaffold does the leaderboard report?",
    "contamination": "Is there evidence/risk of contamination?",
    "human_completability": "Has human completability been assessed?",
    "score_range": "Score range (if possible to estimate)",
    "statistical_adequacy": (
        "Statistical adequacy: How many runs/model (≥ 5 recommended for error bars)"
    ),
    "construct_validity": "Construct validity",
}
RUBRIC_VERSION_LINE = (
    "Rubric v1. We expect future revisions to account for new types of benchmark flaws."
)
SCORING_INTRO = (
    "This is the minimum standard required to be Verified; failing any of these items "
    "results in a Flawed verdict."
)
QUALITY_INTRO = (
    "This is the standard we would like all benchmarks to meet, but it is not necessarily "
    "disqualifying to omit or fail these items."
)
DISCLAIMER = (
    "We undertook this audit without requesting additional information from {creator} to "
    "provide impartial feedback."
)

# where a finding lives: the gate, a defect class, or a quality question
SECTIONS: tuple[str, ...] = ("reviewability", *MINIMUM_STANDARD, *QUALITY)

VERDICTS = ("Verified", "Flawed", "NEI", "Incomplete")
DEFAULT_SCORING_THRESHOLD = 0.2

# the reader's budget. Words per section, sentences per paragraph and per bullet, and
# words per rubric note. Enforced on the records so the rendered review stays short.
WORD_LIMITS = {
    "summary": 150,
    "methodology": 125,
    "interpretation": 300,
    "task_analysis": 300,
    "elicitation_and_scaffolding": 300,
}
SENTENCES_PER_PARAGRAPH = 5
SENTENCES_PER_BULLET = 3
NOTE_WORDS = 80
NOTE_SENTENCES = 4
EXAMPLE_CELL_WORDS = 30

LIMITATIONS_LEAD = (
    "While none of these limitations cross our threshold for a Flawed verdict, they do "
    "inform how a reader should characterize this benchmark."
)
ERRORS_LEAD = "The following issues cross our threshold for a Flawed verdict."
UNRESOLVED_LEAD = "The review did not reach a verdict. These points say why."


def words(text: str) -> int:
    return len(text.split())


def sentences(text: str) -> int:
    """Sentences in a run of prose: terminators followed by a capital, a quote or the end."""
    text = text.strip()
    if not text:
        return 0
    return len(re.findall(r"[.!?]+(?=\s+[A-Z\"'(\[`*]|\s*$)", text)) or 1


def paragraphs(text: str) -> list[str]:
    return [p.strip() for p in re.split(r"\n\s*\n", text.strip()) if p.strip()]


def _check_prose(name: str, text: str, *, max_words: int | None = None) -> None:
    if max_words is not None and words(text) > max_words:
        raise ValueError(f"{name}: {words(text)} words, limit {max_words}")
    for i, para in enumerate(paragraphs(text), 1):
        if sentences(para) > SENTENCES_PER_PARAGRAPH:
            raise ValueError(
                f"{name}: paragraph {i} has {sentences(para)} sentences, limit {SENTENCES_PER_PARAGRAPH}"
            )


def _check_bullets(name: str, items: list[str]) -> None:
    for i, item in enumerate(items, 1):
        if not item.strip():
            raise ValueError(f"{name}: bullet {i} is empty")
        if sentences(item) > SENTENCES_PER_BULLET:
            raise ValueError(
                f"{name}: bullet {i} has {sentences(item)} sentences, limit {SENTENCES_PER_BULLET}"
            )


def _check_note(name: str, text: str) -> None:
    if words(text) > NOTE_WORDS:
        raise ValueError(f"{name} notes: {words(text)} words, limit {NOTE_WORDS}")
    if sentences(text) > NOTE_SENTENCES:
        raise ValueError(f"{name} notes: {sentences(text)} sentences, limit {NOTE_SENTENCES}")


class ReviewabilityRow(BaseModel):
    """Section 1: can the benchmark be reviewed at all."""

    model_config = ConfigDict(extra="forbid")
    level: Reviewability = "Not Reviewed"
    notes: str = ""
    evidence: list[EvidenceRef] = Field(default_factory=list)

    @model_validator(mode="after")
    def _short(self) -> "ReviewabilityRow":
        _check_note("reviewability", self.notes)
        return self


class MinimumStandardRow(BaseModel):
    """Section 2: one defect class; a Flag on any of them makes the benchmark Flawed."""

    model_config = ConfigDict(extra="forbid")
    id: str
    status: MinimumStatus = "Not Reviewed"
    notes: str = ""
    evidence: list[EvidenceRef] = Field(default_factory=list)
    # the scoring row's own count, used when no coverage.json is available
    inspected: int | None = None
    with_defect: int | None = None
    threshold: float = DEFAULT_SCORING_THRESHOLD
    # Epoch's threshold is a default: a status that disagrees with the computed
    # prevalence is allowed only with the reason written down
    threshold_override_reason: str | None = None

    @model_validator(mode="after")
    def _known(self) -> "MinimumStandardRow":
        if self.id not in MINIMUM_STANDARD:
            raise ValueError(
                f"unknown defect class {self.id!r}; one of {', '.join(MINIMUM_STANDARD)}"
            )
        if self.id != "scoring" and (
            self.inspected is not None
            or self.with_defect is not None
            or self.threshold_override_reason is not None
        ):
            raise ValueError(f"{self.id}: counts and threshold overrides belong to the scoring row")
        if not 0 < self.threshold <= 1:
            raise ValueError("threshold must be a fraction in (0, 1]")
        if (self.inspected is None) != (self.with_defect is None):
            raise ValueError("scoring: give both inspected and with_defect, or neither")
        if (
            self.inspected is not None
            and self.with_defect is not None
            and (self.inspected < 0 or not 0 <= self.with_defect <= self.inspected)
        ):
            raise ValueError("scoring: 0 <= with_defect <= inspected")
        _check_note(self.id, self.notes)
        return self


class QualityRow(BaseModel):
    """Section 3: one evaluation-quality question; informative, not disqualifying."""

    model_config = ConfigDict(extra="forbid")
    id: str
    status: str = "Not Reviewed"
    fields: dict[str, str | float | int | None] = Field(default_factory=dict)
    notes: str = ""
    evidence: list[EvidenceRef] = Field(default_factory=list)

    @model_validator(mode="after")
    def _known(self) -> "QualityRow":
        if self.id not in QUALITY:
            raise ValueError(f"unknown quality question {self.id!r}; one of {', '.join(QUALITY)}")
        allowed = QUALITY[self.id][1]
        if self.status not in allowed:
            raise ValueError(f"{self.id}: status must be one of {', '.join(allowed)}")
        expected = set(QUALITY_FIELDS.get(self.id, ()))
        if unknown := set(self.fields) - expected:
            raise ValueError(f"{self.id}: unknown fields {', '.join(sorted(unknown))}")
        if self.status not in ("Not Reviewed", "Unknown") and (
            missing := expected - set(self.fields)
        ):
            raise ValueError(
                f"{self.id}: an assessed row needs fields {', '.join(sorted(missing))}"
            )
        _check_note(self.id, self.notes)
        return self


class ErrorExample(BaseModel):
    """One row of the representative-errors table in a Flawed review."""

    model_config = ConfigDict(extra="forbid")
    task: str = Field(min_length=1)
    error_type: str = Field(min_length=1)
    notes: str = Field(min_length=1)
    affected_logs: str = Field(min_length=1)

    @model_validator(mode="after")
    def _short(self) -> "ErrorExample":
        for name in ("task", "error_type", "notes", "affected_logs"):
            if words(getattr(self, name)) > EXAMPLE_CELL_WORDS:
                raise ValueError(
                    f"error example {self.task}: {name} over {EXAMPLE_CELL_WORDS} words"
                )
        return self


class Review(BaseModel):
    """The whole Epoch review; publication snapshots this same record.

    The rubric rows decide the verdict. The narrative fields are what a reader sees
    first; which of them are required depends on the verdict, and that is checked at
    publication (see `check_narrative`), while their length is checked here.
    """

    model_config = ConfigDict(extra="forbid")
    benchmark: str = Field(min_length=1)
    benchmark_creator: str = ""
    # ISO date; filled with the publication date when left empty
    review_date: str = ""
    reviewability: ReviewabilityRow = Field(default_factory=ReviewabilityRow)
    minimum_standard: list[MinimumStandardRow]
    quality: list[QualityRow]
    # narrative, in reading order. Every verdict: summary and methodology.
    summary: str = ""
    methodology: str = ""
    # Verified: how to read the number, the tasks and grader, the harness; then caveats.
    interpretation: str = ""
    task_analysis: str = ""
    elicitation_and_scaffolding: str = ""
    limitations: list[str] = Field(default_factory=list)
    # what the benchmark's creator should change, as bullets
    recommendations: list[str] = Field(default_factory=list)
    # Flawed: what went wrong, as bullets and as a table of examples.
    representative_errors: list[str] = Field(default_factory=list)
    error_examples: list[ErrorExample] = Field(default_factory=list)

    @model_validator(mode="after")
    def _complete(self) -> "Review":
        for name, rows, expected in (
            ("minimum_standard", self.minimum_standard, MINIMUM_STANDARD),
            ("quality", self.quality, QUALITY),
        ):
            ids = [r.id for r in rows]
            if sorted(ids) != sorted(expected):
                raise ValueError(
                    f"{name} must contain every row exactly once: {', '.join(expected)}"
                )
        for row in (*self.minimum_standard, *self.quality):
            assessed = row.status not in ("Not Reviewed", "Unknown")
            if assessed and not row.evidence:
                raise ValueError(f"{row.id}: an assessed row needs at least one evidence reference")
        if self.reviewability.level != "Not Reviewed" and not self.reviewability.evidence:
            raise ValueError(
                "reviewability: an assessed level needs at least one evidence reference"
            )
        if self.review_date:
            date.fromisoformat(self.review_date)
        for name, limit in WORD_LIMITS.items():
            _check_prose(name, getattr(self, name), max_words=limit)
        _check_bullets("limitations", self.limitations)
        _check_bullets("recommendations", self.recommendations)
        _check_bullets("representative_errors", self.representative_errors)
        return self

    def row(self, id: str) -> MinimumStandardRow:
        return next(r for r in self.minimum_standard if r.id == id)


def skeleton(benchmark: str) -> dict[str, Any]:
    """A review with every row present and nothing assessed, for the agent to fill in."""
    return Review(
        benchmark=benchmark.split("/")[-1],  # a registry name's package prefix is not a title
        minimum_standard=[MinimumStandardRow(id=i) for i in MINIMUM_STANDARD],
        quality=[QualityRow(id=i) for i in QUALITY],
    ).model_dump()


def prepare_review(report: Path, benchmark: str) -> None:
    """Stage the schema, the skeleton and the format marker for a new investigation."""
    from ._report import Finding

    (report / "format.json").write_text(json.dumps({"format": FORMAT}, indent=2) + "\n")
    (report / "review.schema.json").write_text(json.dumps(Review.model_json_schema(), indent=2))
    if not (report / "review.json").exists():
        (report / "review.json").write_text(json.dumps(skeleton(benchmark), indent=2) + "\n")
    schema = Finding.model_json_schema()
    schema["properties"]["section"] = {
        "title": "Section",
        "type": "string",
        "enum": list(SECTIONS),
        "description": "The review row this finding belongs to.",
    }
    (report / "findings.schema.json").write_text(json.dumps(schema, indent=2))


def prevalence(review: Review, coverage: dict[str, Any] | None) -> dict[str, Any]:
    """Defect prevalence among inspected questions, with its source and denominator.

    "Inspected" means a question with a resolved label: NO_ISSUE_FOUND or DEFECT.
    Unresolved and unassessed questions are reported, not counted either way.
    """
    scoring = review.row("scoring")
    if coverage is not None:
        counts = coverage.get("counts") or {}
        defects = int(counts.get("DEFECT", 0))
        clean = int(counts.get("NO_ISSUE_FOUND", 0))
        inspected = defects + clean
        source = "coverage.json"
        extra = {
            "unresolved": int(counts.get("UNRESOLVED", 0)),
            "not_assessed": int(counts.get("NOT_ASSESSED", 0)),
            "population": int(coverage.get("denominator") or 0),
        }
    elif scoring.inspected is not None and scoring.with_defect is not None:
        defects, inspected, source, extra = (
            scoring.with_defect,
            scoring.inspected,
            "review.json",
            {},
        )
    else:
        return {
            "source": None,
            "inspected": 0,
            "with_defect": 0,
            "rate": None,
            "threshold": scoring.threshold,
            "exceeded": None,
        }
    rate = defects / inspected if inspected else None
    return {
        "source": source,
        "inspected": inspected,
        "with_defect": defects,
        "rate": rate,
        "threshold": scoring.threshold,
        "exceeded": None if rate is None else rate >= scoring.threshold,
        **extra,
    }


def derive_verdict(review: Review, coverage: dict[str, Any] | None) -> dict[str, Any]:
    """Apply Epoch's stop rules to the recorded rows.

    Raises when the scoring row's status contradicts the computed prevalence without a
    written reason, so the published verdict and its numbers cannot disagree silently.
    """
    reasons: list[str] = []
    scoring = review.row("scoring")
    prev = prevalence(review, coverage)
    if prev["exceeded"] is not None and scoring.status in ("Pass", "Flag"):
        flagged = scoring.status == "Flag"
        if flagged != prev["exceeded"] and not scoring.threshold_override_reason:
            raise ValueError(
                f"scoring is {scoring.status} but {prev['with_defect']}/{prev['inspected']} inspected "
                f"questions ({prev['rate']:.0%}) are {'at or above' if prev['exceeded'] else 'below'} "
                f"the {prev['threshold']:.0%} threshold ({prev['source']}); set "
                "threshold_override_reason on the scoring row or change the status"
            )
        if flagged != prev["exceeded"]:
            reasons.append(f"scoring threshold overridden: {scoring.threshold_override_reason}")

    level = review.reviewability.level
    if level == "Inadequate":
        verdict = "NEI"
        reasons.insert(0, "reviewability is Inadequate: not enough information to review")
    elif level == "Not Reviewed":
        verdict = "Incomplete"
        reasons.insert(0, "reviewability has not been assessed")
    else:
        statuses = {r.id: r.status for r in review.minimum_standard}
        flagged = [MINIMUM_STANDARD[i] for i, s in statuses.items() if s == "Flag"]
        unreviewed = [MINIMUM_STANDARD[i] for i, s in statuses.items() if s == "Not Reviewed"]
        if flagged:
            verdict = "Flawed"
            reasons.insert(0, "flagged: " + ", ".join(flagged))
        elif unreviewed:
            verdict = "Incomplete"
            reasons.insert(0, "minimum standard not reviewed: " + ", ".join(unreviewed))
        else:
            verdict = "Verified"
            reasons.insert(0, "reviewability " + level + "; every minimum-standard class passes")
    if prev["rate"] is not None:
        reasons.append(
            f"scoring prevalence {prev['with_defect']}/{prev['inspected']} inspected "
            f"({prev['rate']:.1%}; threshold {prev['threshold']:.0%}; {prev['source']})"
        )
    return {"verdict": verdict, "reasons": reasons, "scoring_prevalence": prev}


def required_narrative(verdict: str) -> list[str]:
    """Which narrative fields a review must carry before it can be published."""
    base = ["summary", "methodology"]
    if verdict == "Verified":
        return [
            *base,
            "interpretation",
            "task_analysis",
            "elicitation_and_scaffolding",
            "limitations",
            "recommendations",
        ]
    if verdict == "Flawed":
        return [*base, "representative_errors", "error_examples", "recommendations"]
    return [*base, "limitations"]


def check_narrative(review: Review, verdict: str) -> None:
    """The sections the verdict calls for are present; the ones it does not are absent."""
    needed = required_narrative(verdict)
    missing = [name for name in needed if not getattr(review, name)]
    if missing:
        raise ValueError(
            f"a {verdict} review needs {', '.join(needed)}; missing or empty: {', '.join(missing)}"
        )
    if not review.benchmark_creator.strip():
        raise ValueError("benchmark_creator is empty")
    if verdict == "Flawed":
        unwanted = [
            n
            for n in ("interpretation", "task_analysis", "elicitation_and_scaffolding")
            if getattr(review, n).strip()
        ]
        if unwanted:
            raise ValueError(
                "a Flawed review has summary, methodology and representative errors only; "
                f"move {', '.join(unwanted)} into the rubric notes or the error bullets"
            )
    elif review.representative_errors or review.error_examples:
        raise ValueError(f"representative errors belong to a Flawed review, not a {verdict} one")


def ordinal_date(iso: str) -> str:
    d = date.fromisoformat(iso)
    suffix = "th" if 11 <= d.day % 100 <= 13 else {1: "st", 2: "nd", 3: "rd"}.get(d.day % 10, "th")
    return f"{d.day}{suffix} {d.strftime('%B %Y')}"


def _cell(text: str) -> str:
    """One table cell: paragraphs become line breaks, pipes are escaped."""
    text = text.strip()
    if not text:
        return ""
    paras = [" ".join(p.split()) for p in re.split(r"\n\s*\n|\n", text) if p.strip()]
    return "<br>".join(paras).replace("|", "\\|")


def _options(options: list[str], chosen: str | None) -> str:
    """The form's option list in one cell, the chosen option in bold."""
    return "<br>".join(f"**{o}**" if o == chosen else o for o in options)


def _bullets(items: list[str]) -> str:
    return "<br>".join(f"• {i}" for i in items)


def _value(v: Any) -> str:
    return "___" if v is None else str(v)


def _quality_options(row: QualityRow) -> str:
    """The quality row's Status cell: every option, with the blanks filled and the choice bold."""
    f = row.fields
    if row.id == "contamination":
        filled = row.status == "Assessed"
        line = (
            f"As of {_value(f.get('as_of'))}: {_value(f.get('tasks_public_pct'))}% of tasks "
            f"public<br>{_value(f.get('solutions_public_pct'))}% of solutions public"
        )
        return _options([line, "Not Reviewed"], line if filled else "Not Reviewed")
    if row.id == "score_range":
        filled = row.status == "Estimated"
        line = f"{_value(f.get('floor'))} floor<br>{_value(f.get('ceiling'))} ceiling"
        return _options([line, "Not Reviewed"], line if filled else "Not Reviewed")
    if row.id == "statistical_adequacy":
        line = f"{_value(f.get('runs_per_model'))} runs/model"
        chosen = {"Known": line, "Unknown": "Unknown", "Not Reviewed": "Not Reviewed"}[row.status]
        return _options([line, "Unknown", "Not Reviewed"], chosen)
    options = list(QUALITY[row.id][1])
    if row.id == "human_completability":
        options = [
            "All tasks",
            "Representative set of tasks",
            "Poor implementation (Unrepresentative set of tasks, unreasonable set of participants)",
            "Not established",
            "Not reviewed",
        ]
        chosen = {
            "Poor implementation": options[2],
            "Not Reviewed": "Not reviewed",
        }.get(row.status, row.status)
        return _options(options, chosen)
    return _options(options, row.status)


def render_markdown(review: Review, derived: dict[str, Any]) -> str:
    """The deliverable, rendered from the records so it cannot drift from them.

    The layout follows Epoch's review form: header, narrative sections chosen by the
    verdict, then the three rubric tables with every row and option printed and the
    applicable one in bold, and the disclaimer.
    """
    verdict = derived["verdict"]
    prev = derived["scoring_prevalence"]
    out: list[str] = [f"# {review.benchmark} Review", ""]
    out += [
        f"Benchmark: {review.benchmark}  ",
        f"Benchmark creator: {review.benchmark_creator or '—'}  ",
        f"Verdict: {verdict}  ",
        f"Review date: {ordinal_date(review.review_date) if review.review_date else '—'}",
        "",
    ]
    out += ["## Summary", "", review.summary.strip(), ""]
    out += ["## Methodology", "", review.methodology.strip(), ""]

    if verdict == "Verified":
        out += ["## Interpretation", "", review.interpretation.strip(), ""]
        out += ["## Task Analysis", "", review.task_analysis.strip(), ""]
        out += [
            "## Elicitation and Scaffolding",
            "",
            review.elicitation_and_scaffolding.strip(),
            "",
        ]
        out += ["## Limitations", "", LIMITATIONS_LEAD, ""]
        out += [f"- {b.strip()}" for b in review.limitations] + [""]
    elif verdict == "Flawed":
        out += ["## Representative Errors", "", ERRORS_LEAD, ""]
        out += [f"- {b.strip()}" for b in review.representative_errors] + [""]
        if review.error_examples:
            out += ["| Task | Error type | Notes | Affected logs |", "|---|---|---|---|"]
            out += [
                f"| {_cell(e.task)} | {_cell(e.error_type)} | {_cell(e.notes)} | {_cell(e.affected_logs)} |"
                for e in review.error_examples
            ]
            out.append("")
    else:
        out += ["## Limitations", "", UNRESOLVED_LEAD, ""]
        out += [f"- {b.strip()}" for b in review.limitations] + [""]
    if review.recommendations:
        out += ["## Recommendations", ""]
        out += [f"- {b.strip()}" for b in review.recommendations] + [""]

    out += ["## Benchmark Review Rubric", "", RUBRIC_VERSION_LINE, ""]

    out += ["### 1. Reviewability", "", "| Level | Meaning | Status | Notes |", "|---|---|---|---|"]
    for level, (meaning, branch) in REVIEWABILITY_LEVELS.items():
        chosen = level == review.reviewability.level
        notes = _cell(review.reviewability.notes) if chosen else ""
        out.append(f"| {f'**{level}**' if chosen else level} | {meaning} | {branch} | {notes} |")
    out.append("")

    out += ["### 2. Scoring", "", SCORING_INTRO, ""]
    if prev["rate"] is not None:
        line = (
            f"Scoring prevalence: {prev['with_defect']} of {prev['inspected']} inspected "
            f"questions ({prev['rate']:.0%}); threshold {prev['threshold']:.0%}."
        )
        if prev.get("unresolved") or prev.get("not_assessed"):
            line += (
                f" Unresolved {prev.get('unresolved', 0)}, not assessed "
                f"{prev.get('not_assessed', 0)} of {prev.get('population', 0)}."
            )
        scoring = review.row("scoring")
        if scoring.threshold_override_reason:
            line += f" Threshold overridden: {scoring.threshold_override_reason}"
        out += [line, ""]
    out += [
        "| Defect Class | Examples | Default Threshold for Flawed | Status | Notes |",
        "|---|---|---|---|---|",
    ]
    for r in review.minimum_standard:
        examples, threshold = MINIMUM_STANDARD_FORM[r.id]
        chosen = {"Pass": "Pass", "Flag": "Flag [stop → Flawed]", "Not Reviewed": "Not Reviewed"}[
            r.status
        ]
        out.append(
            f"| {MINIMUM_STANDARD[r.id]} | {_bullets(examples)} | {threshold} | "
            f"{_options(list(MINIMUM_STATUS_FORM), chosen)} | {_cell(r.notes)} |"
        )
    out.append("")

    out += [
        "### 3. Evaluation Quality",
        "",
        QUALITY_INTRO,
        "",
        "| Question | Status | Notes |",
        "|---|---|---|",
    ]
    out += [
        f"| {QUALITY_QUESTIONS[r.id]} | {_quality_options(r)} | {_cell(r.notes)} |"
        for r in review.quality
    ]
    out.append("")

    out += [
        "## Disclaimer",
        "",
        DISCLAIMER.format(creator=review.benchmark_creator or "the benchmark's creator"),
        "",
    ]
    return "\n".join(out)


def load_review(report: Path) -> Review:
    return TypeAdapter(Review).validate_json((report / "review.json").read_text())


def prepare_publication(root: Path, findings: list[dict[str, Any]]) -> dict[str, Any]:
    """Validate the review, derive the verdict and render the markdown; returns the verdict."""
    from ._report import check_evidence_path

    report = root / "work/report"
    review = load_review(report)
    for row in (review.reviewability, *review.minimum_standard, *review.quality):
        for evidence in row.evidence:
            check_evidence_path(root, evidence.path)
    coverage_path = report / "coverage.json"
    coverage = json.loads(coverage_path.read_text()) if coverage_path.is_file() else None
    derived = derive_verdict(review, coverage)
    check_narrative(review, derived["verdict"])
    if not review.review_date:
        review.review_date = datetime.now(UTC).date().isoformat()
        (report / "review.json").write_text(json.dumps(review.model_dump(), indent=2) + "\n")
    derived["findings"] = [
        {"id": f["id"], "section": f["section"], "status": f["status"]}
        for f in findings
        if f.get("status") in ("supported", "qualified")
    ]
    (report / "verdict.json").write_text(json.dumps(derived, indent=2) + "\n")
    (report / "epoch_review.md").write_text(render_markdown(review, derived))
    return derived
