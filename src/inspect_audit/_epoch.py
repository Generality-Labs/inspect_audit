"""The Epoch AI benchmark review as a report format.

Epoch's methodology is a decision procedure, not a parallel rating of dimensions: a
reviewability gate (stop at Not Enough Information), four minimum-standard defect
classes any one of which can make the benchmark Flawed, then seven evaluation-quality
questions that are desirable but not disqualifying. The agent records each row in
`report/review.json`; the verdict is derived here, never chosen by the model, and the
scoring row's prevalence is computed from the question-label coverage the per-item
auditors produce. `epoch_review.md` is rendered from those records, the way the GL
path renders `assessments.tex` from `assessments.json`.
"""

import json
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

# where a finding lives: the gate, a defect class, or a quality question
SECTIONS: tuple[str, ...] = ("reviewability", *MINIMUM_STANDARD, *QUALITY)

VERDICTS = ("Verified", "Flawed", "NEI", "Incomplete")
DEFAULT_SCORING_THRESHOLD = 0.2


class ReviewabilityRow(BaseModel):
    """Section 1: can the benchmark be reviewed at all."""

    model_config = ConfigDict(extra="forbid")
    level: Reviewability = "Not Reviewed"
    notes: str = ""
    evidence: list[EvidenceRef] = Field(default_factory=list)


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
        return self


class Review(BaseModel):
    """The whole Epoch review; publication snapshots this same record."""

    model_config = ConfigDict(extra="forbid")
    benchmark: str = Field(min_length=1)
    reviewability: ReviewabilityRow = Field(default_factory=ReviewabilityRow)
    minimum_standard: list[MinimumStandardRow]
    quality: list[QualityRow]
    summary: str = ""
    limitations: str = ""

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
        if self.reviewability.level not in ("Not Reviewed",) and not self.reviewability.evidence:
            raise ValueError(
                "reviewability: an assessed level needs at least one evidence reference"
            )
        return self

    def row(self, id: str) -> MinimumStandardRow:
        return next(r for r in self.minimum_standard if r.id == id)


def skeleton(benchmark: str) -> dict[str, Any]:
    """A review with every row present and nothing assessed, for the agent to fill in."""
    return Review(
        benchmark=benchmark,
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


def _cell(text: str) -> str:
    return " ".join(text.split()).replace("|", "\\|") or "—"


def _evidence(rows: list[EvidenceRef]) -> str:
    return "<br>".join(f"`{e.path}` {_cell(e.location)}" for e in rows) or "—"


def _value(v: Any) -> str:
    return "___" if v is None else str(v)


def render_markdown(review: Review, derived: dict[str, Any], findings: list[dict[str, Any]]) -> str:
    """The deliverable, rendered from the records so it cannot drift from them."""
    prev = derived["scoring_prevalence"]
    out = [f"# {review.benchmark}: benchmark review", ""]
    out += [f"**Verdict: {derived['verdict']}**", ""]
    out += [f"- {r}" for r in derived["reasons"]] + [""]
    if review.summary.strip():
        out += ["## Summary", "", review.summary.strip(), ""]

    r = review.reviewability
    out += [
        "## 1. Reviewability",
        "",
        "| Level | Notes | Evidence |",
        "|---|---|---|",
        f"| {r.level} | {_cell(r.notes)} | {_evidence(r.evidence)} |",
        "",
    ]

    out += [
        "## 2. Scoring (minimum standard)",
        "",
        "Failing any item results in a Flawed verdict.",
        "",
        "| Defect class | Status | Notes | Evidence |",
        "|---|---|---|---|",
    ]
    for row in review.minimum_standard:
        notes = row.notes
        if row.id == "scoring" and prev["rate"] is not None:
            notes = (
                f"{prev['with_defect']}/{prev['inspected']} inspected questions "
                f"({prev['rate']:.1%}) vs {prev['threshold']:.0%} threshold ({prev['source']}). "
                + notes
            )
            if prev.get("unresolved") or prev.get("not_assessed"):
                notes += (
                    f" Unresolved {prev.get('unresolved', 0)}, not assessed "
                    f"{prev.get('not_assessed', 0)} of {prev.get('population', 0)}."
                )
            if row.threshold_override_reason:
                notes += f" Threshold override: {row.threshold_override_reason}"
        out.append(
            f"| {MINIMUM_STANDARD[row.id]} | {row.status} | {_cell(notes)} | {_evidence(row.evidence)} |"
        )
    out.append("")

    out += [
        "## 3. Evaluation quality",
        "",
        "The standard all benchmarks should meet; omitting or failing these is not disqualifying.",
        "",
        "| Question | Status | Notes | Evidence |",
        "|---|---|---|---|",
    ]
    for row in review.quality:
        name, _ = QUALITY[row.id]
        status = row.status
        if row.id == "contamination" and row.status == "Assessed":
            f = row.fields
            status = (
                f"As of {_value(f.get('as_of'))}: {_value(f.get('tasks_public_pct'))}% of tasks public, "
                f"{_value(f.get('solutions_public_pct'))}% of solutions public"
            )
        elif row.id == "score_range" and row.status == "Estimated":
            status = f"{_value(row.fields.get('floor'))} floor, {_value(row.fields.get('ceiling'))} ceiling"
        elif row.id == "statistical_adequacy" and row.status == "Known":
            status = f"{_value(row.fields.get('runs_per_model'))} runs/model"
        out.append(f"| {name} | {_cell(status)} | {_cell(row.notes)} | {_evidence(row.evidence)} |")
    out.append("")

    supported = [f for f in findings if f.get("status") in ("supported", "qualified")]
    if supported:
        out += [
            "## Findings register",
            "",
            "| ID | Section | Status | Claim |",
            "|---|---|---|---|",
        ]
        for f in supported:
            out.append(f"| {f['id']} | {f['section']} | {f['status']} | {_cell(f['claim'])} |")
        out.append("")
    if review.limitations.strip():
        out += ["## Limitations", "", review.limitations.strip(), ""]
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
    (report / "verdict.json").write_text(json.dumps(derived, indent=2) + "\n")
    (report / "epoch_review.md").write_text(render_markdown(review, derived, findings))
    return derived
