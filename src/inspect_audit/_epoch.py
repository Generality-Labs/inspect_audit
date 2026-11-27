"""The Epoch AI benchmark review, from a frozen findings bundle.

One description, `audit/epoch.json`: the methodology (the rubric's checks, their scales,
the scoring threshold, the verdict rule and the sampling policy) and the page template
(header, prose blocks with their limits, rubric layout). This module loads it, reads
the review out of a findings bundle (the auditor's ratings keyed by check, the sample
labels standing in the register), derives the verdict by the methodology's procedure,
computes the scoring measure from the labels, checks the prose the publisher is given,
and renders the page. No rubric text, scale or limit lives here.

The review has two phases, run in one audit. Phase 1 audits the tasks without
reading evaluation logs: the reviewability, scoring and consistency rows, nothing
else. An audit given no logs stops there, and a Flag ends it as Flawed while a pass
leaves it Incomplete. An audit given logs (`subject.supplied_logs`) goes on to phase
2 in the same run: the rows the methodology marks `needs_logs`, and it can reach
Verified.
"""

import json
import os
import re
from datetime import UTC, date, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ._labels import (
    CLASS_TITLES,
    EPOCH_CLASSES,
    GRADING_CLASSES,
    SUB_REASONS,
    Source,
    legacy_class,
    majority_label,
)
from ._verdicts import Evidence

if TYPE_CHECKING:
    from ._findings import Findings

DESCRIPTION = Path(__file__).parent / "audit/epoch.json"

# the checks whose per-sample labels decide the scoring row: the item as written, and
# (with logs) its recorded grades. A sample defective under either counts once.
LABEL_CHECKS = ("question-labels", "grading-labels")
# how the page names each label check's defects in the prevalence line
CHECK_PHRASES = {"question-labels": "defective as written", "grading-labels": "misgraded"}
STATES = ("NO_ISSUE_FOUND", "DEFECT", "UNRESOLVED", "NOT_ASSESSED")
UNASSESSED = ("Not Reviewed", "Not reviewed", "Unknown")

# the scoring row's `fields` that describe the sample rather than the row: how the
# audited items were chosen decides whether their defect rate measures prevalence
SAMPLE_DESIGNS = ("random", "stratified", "census", "purposive")
SAMPLING_FIELDS = ("sample_design", "drawn_by", "population")


# ------------------------------------------------------------------ description


class Threshold(BaseModel):
    model_config = ConfigDict(extra="forbid")
    measure: str
    flag_when: str  # a comparison against the measure's value, e.g. ">= 0.20"
    override: str = ""

    def exceeded(self, value: float) -> bool:
        match = re.fullmatch(r"\s*(>=|<=|>|<|==)\s*([0-9.]+)\s*", self.flag_when)
        if not match:
            raise ValueError(f"cannot read threshold {self.flag_when!r}")
        op, bound = match.group(1), float(match.group(2))
        return {
            ">=": value >= bound,
            "<=": value <= bound,
            ">": value > bound,
            "<": value < bound,
            "==": value == bound,
        }[op]

    @property
    def bound(self) -> float:
        return float(re.findall(r"[0-9.]+", self.flag_when)[0])


class Check(BaseModel):
    """One row of the rubric: its scale, its fixed form text and what an assessment needs."""

    model_config = ConfigDict(extra="forbid")
    id: str
    section: str
    name: str
    question: str
    guidance: str = ""
    scale: list[str]
    form: dict[str, Any] | str | None = None
    fields: list[str] = Field(default_factory=list)
    flag_requires_impact: bool = False
    # answered from evaluation logs: without them the check stays unassessed
    needs_logs: bool = False
    notes_limit: dict[str, int] | None = None
    impact_limit: dict[str, int] | None = None
    threshold: Threshold | None = None
    evidence: list[str] = Field(default_factory=list)
    taxonomy: dict[str, list[str]] = Field(default_factory=dict)

    @property
    def unassessed_levels(self) -> set[str]:
        return {s for s in self.scale if s in UNASSESSED}


class VerdictProcedure(BaseModel):
    model_config = ConfigDict(extra="forbid")
    gate: dict[str, str]
    minimum: dict[str, str]
    unfinished: dict[str, str]
    otherwise: str


class VerdictRule(BaseModel):
    model_config = ConfigDict(extra="forbid")
    scale: list[str]
    rule: str
    stop: str = ""
    flag: str = ""
    procedure: VerdictProcedure
    impact: dict[str, int] = Field(default_factory=lambda: {"max_words": 60, "max_sentences": 3})


class MeasureSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    numerator: str
    denominator: str
    note: str = ""


class SamplingPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")
    unit: str
    population: str
    sample: str
    expansion: str = ""
    confirmation: str = ""
    states: list[str]
    measure: MeasureSpec


class Methodology(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    version: int
    status: Literal["draft", "current", "superseded"]
    source: str = ""
    notes: str = ""
    intro: dict[str, str] = Field(default_factory=dict)
    verdict: VerdictRule
    sampling: SamplingPolicy
    checks: list[Check]
    notes_limit: dict[str, int] = Field(
        default_factory=lambda: {"max_words": 80, "max_sentences": 4}
    )

    @model_validator(mode="after")
    def _consistent(self) -> "Methodology":
        ids = [c.id for c in self.checks]
        if len(set(ids)) != len(ids):
            raise ValueError("check ids must be unique")
        proc = self.verdict.procedure
        if proc.gate["check"] not in ids:
            raise ValueError(f"verdict gate names unknown check {proc.gate['check']!r}")
        if not self.by_section(proc.minimum["section"]):
            raise ValueError(f"verdict minimum section {proc.minimum['section']!r} has no checks")
        for name in (
            proc.gate["outcome"],
            proc.minimum["outcome"],
            proc.unfinished["outcome"],
            proc.otherwise,
        ):
            if name not in self.verdict.scale:
                raise ValueError(f"verdict outcome {name!r} is not on the verdict scale")
        for check in self.checks:
            if check.threshold and check.threshold.measure != self.sampling.measure.name:
                raise ValueError(f"{check.id}: threshold measure is not the sampling measure")
        return self

    @property
    def key(self) -> str:
        return f"{self.id}@{self.version}"

    def check(self, id: str) -> Check:
        for c in self.checks:
            if c.id == id:
                return c
        raise KeyError(f"{self.key} has no check {id!r}")

    def by_section(self, section: str) -> list[Check]:
        return [c for c in self.checks if c.section == section]

    @property
    def sections(self) -> list[str]:
        return list(dict.fromkeys(c.section for c in self.checks))

    @property
    def gate(self) -> Check:
        return self.check(self.verdict.procedure.gate["check"])

    @property
    def minimum(self) -> list[Check]:
        return self.by_section(self.verdict.procedure.minimum["section"])

    @property
    def quality(self) -> list[Check]:
        exclude = {self.gate.id} | {c.id for c in self.minimum}
        return [c for c in self.checks if c.id not in exclude]

    @property
    def check_ids(self) -> tuple[str, ...]:
        return tuple(c.id for c in self.checks)

    @property
    def thresholded(self) -> list[Check]:
        return [c for c in self.checks if c.threshold is not None]


class Column(BaseModel):
    model_config = ConfigDict(extra="forbid")
    key: str
    title: str
    max_words: int | None = None
    max_sentences: int | None = None
    # one sentence about the item for its reader, with no account of the audit
    plain: bool = False
    # each row's own text: a run of words repeated from another row's cell is reported
    # by the preview, as a row edited into the wrong place
    distinct: bool = False


class Block(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    title: str = ""
    kind: Literal["prose", "bullets", "table"]
    brief: str = ""
    max_words: int | None = None
    max_sentences: int | None = None
    max_sentences_per_paragraph: int | None = None
    max_cell_words: int | None = None
    max_items: int | None = None
    plain: bool = False
    max_paragraphs: int | None = None
    columns: list[Column] = Field(default_factory=list)
    when: list[str]
    required: list[str] = Field(default_factory=list)
    required_when_defects: bool = False
    closing: bool = False
    lead: str | dict[str, str] = ""
    footer: Literal["", "coverage_ids"] = ""
    # a table of items: after it, each item's competing answers with their sources,
    # from the findings, never from the prose
    sources: bool = False
    # a table of items grouped by Epoch's defect class, in Epoch's order, each group
    # with its count; this column prints the item's sub-reason from the findings
    by_class: str = ""

    def shown_for(self, verdict: str) -> bool:
        return verdict in self.when

    def column(self, key: str) -> Column:
        return next(c for c in self.columns if c.key == key)

    def required_for(self, verdict: str) -> bool:
        return verdict in self.required

    def lead_for(self, verdict: str) -> str:
        if isinstance(self.lead, str):
            return self.lead
        return self.lead.get(verdict, self.lead.get("default", ""))


class HeaderLine(BaseModel):
    model_config = ConfigDict(extra="forbid")
    label: str
    value: str
    format: str = ""


class RubricSection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    section: str
    title: str
    intro: str = ""
    layout: Literal["levels", "defect_classes", "questions"]
    columns: list[str]
    measure_line: bool = False


class Rubric(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str
    intro: str = ""
    sections: list[RubricSection]
    disclaimer: str = ""


class Footnotes(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = "Footnotes"
    max_words: int = 60
    max_items: int = 12
    brief: str = ""


class Examined(BaseModel):
    """The representative errors, filled out when few defects are confirmed (both pages).

    Under the confirmed defects (unchanged), the most important other items the review
    examined: unresolved items first, then items a check labelled a defect that the
    standing labels cleared, up to `fill_to` items in all, each with its verdict label
    and the recorded evidence quoted from the findings.
    """

    model_config = ConfigDict(extra="forbid")
    fill_to: int = 5
    title: str = "Other items examined"
    # the section's lead; `none_lead` opens the representative errors when the prose has
    # none because nothing was confirmed
    lead: str = ""
    none_lead: str = ""
    # the verdict label per standing: DEFECT, UNRESOLVED, and CLEARED (a defect label
    # the standing labels overturned)
    labels: dict[str, str] = Field(default_factory=dict)
    # what each label check judged, for the reader
    checks: dict[str, str] = Field(default_factory=dict)
    max_quote_words: int = 45
    max_text_words: int = 60


class Template(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    version: int
    methodology: str
    audience: str = ""
    style: dict[str, Any] = Field(default_factory=dict)
    title: str
    header: list[HeaderLine]
    notices: dict[str, str] = Field(default_factory=dict)
    blocks: list[Block]
    footnotes: Footnotes | None = None
    rubric: Rubric
    # what the page opens with after its header, in order: "rubric" and block ids. The
    # notices and the other blocks follow, in template order; without it the blocks
    # come first and the rubric after them
    opening: list[str] = Field(default_factory=list)
    # the fill for the representative errors when few defects are confirmed
    examined: Examined | None = None
    # the page ends after the opening and the notices: the other blocks and the
    # disclaimer are not rendered, so none is required (one written is still checked
    # and kept in prose.json), and only the footnotes the page refers to are printed
    ends_after_opening: bool = False

    @model_validator(mode="after")
    def _consistent(self) -> "Template":
        ids = [b.id for b in self.blocks]
        if len(set(ids)) != len(ids):
            raise ValueError("block ids must be unique")
        if unknown := set(self.opening) - set(ids) - {"rubric"}:
            raise ValueError(f"opening names unknown blocks {', '.join(sorted(unknown))}")
        if any(b.closing for b in self.blocks if b.id in self.opening):
            raise ValueError("a closing block cannot open the page")
        if self.ends_after_opening and "rubric" not in self.opening:
            raise ValueError("a page that ends after its opening must open with the rubric")
        default = self.style.get("max_sentences_per_paragraph")
        for block in self.blocks:
            if block.kind == "table" and not block.columns:
                raise ValueError(f"{block.id}: a table block needs columns")
            if block.kind == "prose" and block.max_sentences_per_paragraph is None and default:
                block.max_sentences_per_paragraph = int(default)
            if set(block.required) - set(block.when):
                raise ValueError(f"{block.id}: required verdicts must be among `when`")
        return self

    def renders(self, block: Block) -> bool:
        """Whether the page prints the block at all (for a verdict that shows it)."""
        return not self.ends_after_opening or block.id in self.opening

    def block(self, id: str) -> Block:
        for b in self.blocks:
            if b.id == id:
                return b
        raise KeyError(f"template {self.id} has no block {id!r}")

    def check_methodology(self, method: Methodology) -> None:
        if self.methodology != method.key:
            raise ValueError(f"template {self.id} renders {self.methodology}, not {method.key}")
        for sec in self.rubric.sections:
            if sec.section not in method.sections:
                raise ValueError(f"template section {sec.section!r} is not in {method.key}")
        for block in self.blocks:
            if unknown := set(block.when) - set(method.verdict.scale):
                raise ValueError(f"{block.id}: unknown verdicts {', '.join(sorted(unknown))}")


def description() -> dict[str, Any]:
    return json.loads(DESCRIPTION.read_text())


def methodology() -> Methodology:
    return Methodology.model_validate(description()["methodology"])


def template(full_page: bool = False) -> Template:
    """The page template; `full_page` renders past the opening, whatever the template says.

    A full page prints every other block the verdict shows, the closing blocks and the
    disclaimer, then the opening, the notices and the footnotes, and each block's
    `required` rule applies. Without it the template is rendered as written.
    """
    doc = description()
    method = Methodology.model_validate(doc["methodology"])
    page = Template.model_validate(doc["template"])
    page.check_methodology(method)
    if full_page:
        page = page.model_copy(update={"ends_after_opening": False})
    return page


def methodology_markdown(method: Methodology, page: Template) -> str:
    """The description as the auditor reads it: the checks, the verdict rule, the blocks."""
    out = [
        f"# {method.id} v{method.version} ({method.status})",
        "",
        method.notes.strip(),
        "",
        "## Verdict",
        "",
        method.verdict.rule.strip(),
        "",
        method.verdict.stop.strip(),
        "",
        method.verdict.flag.strip(),
        "",
        "## Sampling",
        "",
        (
            f"Unit: {method.sampling.unit}. Population: {method.sampling.population}. "
            f"Sample: {method.sampling.sample}"
        ),
        "",
        (
            f"Measure `{method.sampling.measure.name}` = {method.sampling.measure.numerator} / "
            f"{method.sampling.measure.denominator}. {method.sampling.measure.note}"
        ),
        "",
        "## Checks",
        "",
        "Rate each check in `auditor.ratings` under its id, with its level as the label.",
        "",
    ]
    for check in method.checks:
        flags = []
        if check.needs_logs:
            flags.append("needs logs (phase 2)")
        if check.threshold:
            flags.append(
                f"threshold: flag when {check.threshold.measure} {check.threshold.flag_when}"
            )
        if check.flag_requires_impact:
            flags.append("a Flag needs `impact`")
        if check.fields:
            flags.append("fields: " + ", ".join(check.fields))
        out += [
            f"### {check.id} ({check.section}): {check.name}",
            "",
            check.question.strip(),
            "",
            f"Scale: {', '.join(check.scale)}" + (f". {'; '.join(flags)}" if flags else ""),
            "",
            check.guidance.strip(),
            "",
        ]
    out += [
        "## Prose blocks",
        "",
        "Written after submission in `/workspace/report/prose.json`.",
        "",
    ]
    if page.opening:
        opening = ", then ".join("the rubric" if i == "rubric" else f"`{i}`" for i in page.opening)
        if page.ends_after_opening:
            out += [
                (
                    f"The page shows, after its header, {opening}, then the notices, and "
                    "ends there. The other blocks below are not rendered: leave them out. "
                    "One written anyway is checked against its limits and kept in prose.json."
                ),
                "",
            ]
        else:
            out += [
                (
                    "The page shows, after its header, every block below that the "
                    "verdict shows except the opening ones, in this order with the "
                    f"closing blocks last, then the disclaimer, then {opening}, the "
                    "notices and the footnotes. "
                    "Write every block marked required for the verdict, within its "
                    "limits: a missing one is refused."
                ),
                "",
            ]
    for block in page.blocks:
        if not page.renders(block):
            out += [f"### {block.id} (not rendered; optional)", ""]
            continue
        limits = [
            f"{k.replace('max_', 'at most ').replace('_', ' ')} {v}"
            for k, v in block.model_dump().items()
            if k.startswith("max_") and v
        ]
        out += [
            f"### {block.id} ({block.kind}; shown for {', '.join(block.when)}"
            + (f"; required for {', '.join(block.required)}" if block.required else "")
            + ")",
            "",
            block.brief.strip(),
            "",
            ("Limits: " + ", ".join(limits)) if limits else "",
            "",
        ]
    if page.footnotes:
        out += [
            "### footnotes",
            "",
            page.footnotes.brief.strip(),
            "",
            f"Limits: at most {page.footnotes.max_items} notes of {page.footnotes.max_words} words",
            "",
        ]
    return "\n".join(line for line in out if line is not None)


def stage(analysis: Path, full_page: bool = False) -> None:
    """Stage the description where the auditor reads it: data and its plain reading."""
    target = analysis / "epoch"
    target.mkdir(parents=True, exist_ok=True)
    (target / "epoch.json").write_bytes(DESCRIPTION.read_bytes())
    (target / "methodology.md").write_text(methodology_markdown(methodology(), template(full_page)))


# ------------------------------------------------------------------ text rules


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


# words that describe the audit rather than the question: a line for the reader of the
# page says what is wrong with the item, not how that was found out
PROCESS_WORDS = frozenset(
    [
        "auditor",
        "auditors",
        "probe",
        "probes",
        "probed",
        "independent",
        "independently",
        "established",
        "unresolved",
        "verified",
        "unverified",
        "candidate",
        "candidates",
        "attempt",
        "attempts",
        "agent",
        "agents",
    ]
)
# a bullet may speak of attempts, agents and candidates: a run-level error is about them
BULLET_PROCESS_WORDS = frozenset(
    [
        "auditor",
        "auditors",
        "probe",
        "probes",
        "probed",
        "independently",
        "established",
        "unresolved",
        "unassessed",
        "verified",
        "unverified",
    ]
)
MODEL_NAMES = re.compile(
    r"\b(gpt|claude|gemini|qwen|llama|deepseek|mistral|grok|opus|sonnet|haiku|luna|astra|terra"
    r"|o[1-9](?:-mini|-pro)?)\b",
    re.IGNORECASE,
)


def check_about_the_benchmark(
    name: str, text: str, vocabulary: frozenset[str] = PROCESS_WORDS
) -> None:
    found = sorted({w for w in re.findall(r"[A-Za-z]+", text.lower()) if w in vocabulary})
    if found:
        raise ValueError(
            f"{name}: says how the audit went ({', '.join(found)}); say what is wrong with "
            "the benchmark instead, as its reader would see it"
        )


def check_no_model_names(name: str, text: str) -> None:
    found = sorted({m.group(0) for m in MODEL_NAMES.finditer(text)}, key=str.lower)
    if found:
        raise ValueError(
            f"{name}: names a model ({', '.join(found)}); a note states the conclusion, "
            "and which model did what is a footnote"
        )


def check_length(name: str, text: str, *, max_words: int | None, max_sentences: int | None) -> None:
    if max_words is not None and words(text) > max_words:
        raise ValueError(f"{name}: {words(text)} words, limit {max_words}")
    if max_sentences is not None and sentences(text) > max_sentences:
        raise ValueError(f"{name}: {sentences(text)} sentences, limit {max_sentences}")


def check_plain(name: str, text: str, *, max_words: int = 25) -> None:
    check_length(name, text, max_words=max_words, max_sentences=1)
    check_about_the_benchmark(name, text)


# ------------------------------------------------------------------ the review in a bundle


class Sampling(BaseModel):
    """How the audited items were chosen, read from the scoring row's fields."""

    model_config = ConfigDict(extra="forbid")
    strategy: str
    sample: int
    population: int | None = None
    drawn_by: str = ""

    @property
    def for_prevalence(self) -> bool:
        return self.strategy in ("random", "stratified", "census")


class Assessment(BaseModel):
    """One check, one level, a short note, the evidence it rests on."""

    model_config = ConfigDict(extra="forbid")
    check: str
    level: str
    notes: str = ""
    evidence: list[Evidence] = Field(default_factory=list)
    fields: dict[str, str | float | int | None] = Field(default_factory=dict)
    impact: str = ""
    threshold_override_reason: str = ""

    @property
    def assessed(self) -> bool:
        return self.level not in UNASSESSED

    def validate_against(self, check: Check, method: Methodology) -> None:
        name = f"ratings.{self.check}"
        if self.level not in check.scale:
            raise ValueError(
                f"{name}: label must be one of {', '.join(check.scale)}, not {self.level!r}"
            )
        expected = set(check.fields)
        if unknown := set(self.fields) - expected:
            raise ValueError(f"{name}: unknown fields {', '.join(sorted(unknown))}")
        if self.assessed and (missing := expected - set(self.fields)):
            raise ValueError(f"{name}: an assessed check needs fields {', '.join(sorted(missing))}")
        if self.assessed and not self.evidence:
            raise ValueError(f"{name}: an assessed check needs at least one evidence reference")
        limit = check.notes_limit or method.notes_limit
        check_length(
            f"{name} explanation",
            self.notes,
            max_words=limit.get("max_words"),
            max_sentences=limit.get("max_sentences"),
        )
        check_no_model_names(f"{name} explanation", self.notes)
        failing = self.level == method.verdict.procedure.minimum["fail_at"]
        if check.flag_requires_impact and failing and not self.impact.strip():
            raise ValueError(
                f"{name}: a {self.level} needs `impact`, the measured effect on the headline "
                "results (with numbers); a defect without a material effect is a "
                f"{check.scale[0]} recorded under limitations"
            )
        if self.impact.strip() and not failing:
            raise ValueError(
                f"{name}: impact belongs to a {method.verdict.procedure.minimum['fail_at']}"
            )
        budget = check.impact_limit or method.verdict.impact
        check_length(
            f"{name} impact",
            self.impact,
            max_words=budget.get("max_words"),
            max_sentences=budget.get("max_sentences"),
        )
        if check.threshold is None and self.threshold_override_reason:
            raise ValueError(f"{name}: a threshold override belongs to a check with a threshold")


def logs_supplied(findings: "Findings") -> bool:
    """Whether the audit was given evaluation logs: phase 2's rows are answerable."""
    supplied = findings.subject.get("supplied_logs")
    return bool(supplied) if isinstance(supplied, list) else False


def _standing_label(takes: list[dict[str, Any]]) -> str | None:
    """The label that stands on a cell.

    The auditor's latest review, else the investigators' majority as the register
    takes it (`majority_label`: one vote per job, ties a defect), else the latest
    verdict; None where nothing was recorded.
    """
    mine = [t for t in takes if t.get("by") == "auditor"]
    if mine:
        return str(mine[-1]["label"])
    if not takes:
        return None
    votes = [(str(t.get("run") or t.get("eval_id") or ""), str(t["label"])) for t in takes]
    return majority_label(votes) or str(takes[-1]["label"])


def _status(label: str | None) -> str:
    if label is None:
        return "NOT_ASSESSED"
    if label in STATES:
        return label
    # a label outside the question-labels vocabulary is a check that could not settle
    return "UNRESOLVED"


def coverage_from(findings: "Findings") -> dict[str, Any] | None:
    """The sample labels as coverage: one status per item, counts, and counts per check.

    The investigators' verdicts are overlaid by the auditor's reviews, as the register
    does. An item is a defect under either label check, clean only when every check
    that ran found nothing, unresolved when a check could not settle it, and not
    assessed when a label check has no verdict on it at all.
    """
    declared = findings.investigators.get("checks")
    checks = [c for c in LABEL_CHECKS if isinstance(declared, dict) and c in declared]
    if not checks:
        return None
    recorded = findings.investigators.get("samples")
    samples: dict[str, Any] = dict(recorded) if isinstance(recorded, dict) else {}
    reviews: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for review in findings.auditor.reviews:
        reviews.setdefault((review.sample_id, review.check), []).append(
            {"by": "auditor", "label": review.label}
        )
    questions = []
    counts = dict.fromkeys(STATES, 0)
    by_check: dict[str, dict[str, int]] = {c: dict.fromkeys(STATES, 0) for c in checks}
    for sample_id, cells in samples.items():
        statuses: dict[str, str] = {}
        for check in checks:
            takes = [*(cells.get(check) or []), *reviews.get((sample_id, check), [])]
            statuses[check] = _status(_standing_label(takes))
            by_check[check][statuses[check]] += 1
        found = set(statuses.values())
        if "DEFECT" in found:
            status = "DEFECT"
        elif "UNRESOLVED" in found:
            status = "UNRESOLVED"
        elif "NOT_ASSESSED" in found:
            status = "NOT_ASSESSED"
        else:
            status = "NO_ISSUE_FOUND"
        counts[status] += 1
        questions.append({"question_id": sample_id, "status": status, "by_check": statuses})
    return {
        "checks": checks,
        "questions": questions,
        "counts": counts,
        "counts_by_check": by_check,
        "denominator": len(questions),
    }


def coverage_ids(coverage: dict[str, Any] | None, status: str) -> list[str]:
    if not coverage:
        return []
    return [str(q["question_id"]) for q in coverage.get("questions") or [] if q["status"] == status]


def has_defects(coverage: dict[str, Any] | None) -> bool:
    return bool(coverage_ids(coverage, "DEFECT"))


def _listed_sources(record: Any, question_id: str) -> list[Any]:
    """The sources an investigator's assessment rows give for one item."""
    rows = record.get("question_assessments") if isinstance(record, dict) else None
    rows = [r for r in rows if isinstance(r, dict)] if isinstance(rows, list) else []
    mine = [r for r in rows if str(r.get("question_id")) == question_id] or rows
    return [s for r in mine for s in (r.get("sources") or [])]


def sources_from(findings: "Findings") -> dict[str, list[dict[str, Any]]]:
    """Each item's competing answers with their sources, as the findings record them.

    Per label check, the auditor's latest review that gives sources, else the latest
    investigator verdict whose assessment row for the item does; both checks' lists
    are joined, an answer at the same address listed once. Findings from before
    sources were recorded give none, and the page says nothing about them.
    """
    recorded = findings.investigators.get("samples")
    samples: dict[str, Any] = dict(recorded) if isinstance(recorded, dict) else {}
    reviewed: dict[tuple[str, str], list[Any]] = {}
    for review in findings.auditor.reviews:
        if review.sources:
            reviewed[(review.sample_id, review.check)] = [s.model_dump() for s in review.sources]
    out: dict[str, list[dict[str, Any]]] = {}
    for sample_id, cells in samples.items():
        if not isinstance(cells, dict):
            continue
        found: list[dict[str, Any]] = []
        for check in LABEL_CHECKS:
            chosen = reviewed.get((sample_id, check))
            if chosen is None:
                for take in reversed(cells.get(check) or []):
                    listed = _listed_sources(
                        take.get("record") if isinstance(take, dict) else None, sample_id
                    )
                    if listed:
                        chosen = listed
                        break
            for raw in chosen or []:
                try:
                    source = Source.model_validate(raw).model_dump()
                except ValueError:
                    continue
                if not any(
                    (f["answer"], f["url"]) == (source["answer"], source["url"]) for f in found
                ):
                    found.append(source)
        if found:
            out[str(sample_id)] = found
    return out


def _row_class(record: Any, question_id: str, *, legacy: bool) -> tuple[str, str] | None:
    """An investigator's class for one item: as recorded, or (legacy) as its types map."""
    rows = record.get("question_assessments") if isinstance(record, dict) else None
    rows = [r for r in rows if isinstance(r, dict)] if isinstance(rows, list) else []
    for row in rows:
        if str(row.get("question_id")) != question_id or row.get("status") != "DEFECT":
            continue
        if not legacy and row.get("epoch_class") and row.get("sub_reason"):
            return str(row["epoch_class"]), str(row["sub_reason"])
        if legacy:
            types = row.get("defect_types")
            return legacy_class([str(t) for t in types] if isinstance(types, list) else [])
    return None


def classes_from(
    findings: "Findings", coverage: dict[str, Any] | None
) -> dict[str, dict[str, str]]:
    """Each defective item's Epoch class and sub-reason, as the findings record them.

    From the label checks under which the item stands as a DEFECT, the item as written
    first: the auditor's latest review that gives a class, else the latest investigator
    row that does. Findings from before classes were recorded have their defect types
    read through `legacy_class` (`basis` says which); an item none of this classifies
    is left out, and the page prints it unclassified.
    """
    recorded = findings.investigators.get("samples")
    samples: dict[str, Any] = dict(recorded) if isinstance(recorded, dict) else {}
    reviewed: dict[tuple[str, str], tuple[str, str]] = {}
    for review in findings.auditor.reviews:
        if review.label == "DEFECT" and review.epoch_class and review.sub_reason:
            reviewed[(review.sample_id, review.check)] = (review.epoch_class, review.sub_reason)
    out: dict[str, dict[str, str]] = {}
    for item in (coverage or {}).get("questions") or []:
        if item["status"] != "DEFECT":
            continue
        qid = str(item["question_id"])
        cells = samples.get(qid) if isinstance(samples.get(qid), dict) else {}
        defective = [c for c in LABEL_CHECKS if (item.get("by_check") or {}).get(c) == "DEFECT"]
        for basis in ("recorded", "legacy"):
            for check in defective:
                found = reviewed.get((qid, check)) if basis == "recorded" else None
                for take in reversed((cells or {}).get(check) or []):
                    if found:
                        break
                    if isinstance(take, dict) and take.get("label") == "DEFECT":
                        found = _row_class(take.get("record"), qid, legacy=basis == "legacy")
                allowed = GRADING_CLASSES if check == "grading-labels" else EPOCH_CLASSES
                if found and found[0] in allowed and found[1] in SUB_REASONS[found[0]]:
                    out[qid] = {
                        "epoch_class": found[0],
                        "sub_reason": found[1],
                        "check": check,
                        "basis": basis,
                    }
                    break
            if qid in out:
                break
    return out


def _clip(text: str, max_words: int) -> str:
    """At most `max_words` words, whole sentences where they fit."""
    text = " ".join(str(text).split())
    if words(text) <= max_words:
        return text
    kept = ""
    for sentence in re.split(r"(?<=[.!?])\s+", text):
        if words(f"{kept} {sentence}") > max_words:
            break
        kept = f"{kept} {sentence}".strip()
    return kept or " ".join(text.split()[:max_words]) + " …"


def examined_from(
    findings: "Findings", coverage: dict[str, Any] | None, limit: int
) -> list[dict[str, Any]]:
    """The most important items examined beyond the confirmed defects, at most `limit`.

    Unresolved items first, then items some label check called a defect that the
    standing labels cleared (most defect labels first), each in sample order. Per item,
    each check that makes it one: the concern a defect label raised, the standing
    label's explanation, and the evidence it recorded, all from the findings.
    """
    if limit <= 0 or not coverage:
        return []
    recorded = findings.investigators.get("samples")
    samples: dict[str, Any] = dict(recorded) if isinstance(recorded, dict) else {}
    takes: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for sample_id, cells in samples.items():
        if not isinstance(cells, dict):
            continue
        for check in LABEL_CHECKS:
            for t in cells.get(check) or []:
                if isinstance(t, dict):
                    takes.setdefault((str(sample_id), check), []).append(t)
    for review in findings.auditor.reviews:
        takes.setdefault((review.sample_id, review.check), []).append(
            {
                "by": "auditor",
                "label": review.label,
                "explanation": review.explanation,
                "evidence": [e.model_dump() for e in review.evidence],
            }
        )

    def standing(cell: list[dict[str, Any]]) -> dict[str, Any] | None:
        mine = [t for t in cell if t.get("by") == "auditor"]
        return (mine or cell or [None])[-1]

    unresolved: list[dict[str, Any]] = []
    cleared: list[tuple[int, dict[str, Any]]] = []
    for q in coverage.get("questions") or []:
        qid, status = str(q["question_id"]), q["status"]
        if status not in ("UNRESOLVED", "NO_ISSUE_FOUND"):
            continue
        parts: list[dict[str, Any]] = []
        raised_total = 0
        for check in LABEL_CHECKS:
            cell = takes.get((qid, check)) or []
            state = (q.get("by_check") or {}).get(check)
            raised = [t for t in cell if t.get("label") == "DEFECT"]
            if status == "UNRESOLVED" and state != "UNRESOLVED":
                continue
            if status == "NO_ISSUE_FOUND" and not raised:
                continue
            raised_total += len(raised)
            final = standing(cell) or {}
            quotes = [
                str(e.get("observed", "")).strip()
                for e in (final.get("evidence") or [])
                if isinstance(e, dict) and str(e.get("observed", "")).strip()
            ]
            parts.append(
                {
                    "check": check,
                    "raised": str(raised[-1].get("explanation", "")) if raised else "",
                    "finding": str(final.get("explanation", "")),
                    "quotes": quotes[:1],
                }
            )
        if not parts:
            continue
        item = {
            "question_id": qid,
            "standing": "UNRESOLVED" if status == "UNRESOLVED" else "CLEARED",
            "checks": parts,
        }
        if status == "UNRESOLVED":
            unresolved.append(item)
        else:
            cleared.append((raised_total, item))
    cleared.sort(key=lambda pair: -pair[0])  # stable: sample order among equals
    return (unresolved + [item for _, item in cleared])[:limit]


def assessments_from(
    findings: "Findings", method: Methodology
) -> tuple[dict[str, Assessment], Sampling | None]:
    """Every check's assessment from the auditor's ratings; a check not rated is unassessed.

    Ratings are keyed by check id. The scoring row's fields describe the sample
    (`sample_design`, `drawn_by`, `population`) and are read into the sampling design
    rather than kept as the row's own fields.
    """
    unfinished = method.verdict.procedure.unfinished["level"]
    rows: dict[str, Assessment] = {}
    sampling: Sampling | None = None
    for key, rating in findings.auditor.ratings.items():
        if key == "overall":
            raise ValueError(
                "ratings.overall is not part of an Epoch review: the verdict is derived "
                "from the rubric rows, never rated"
            )
        if key not in method.check_ids:
            raise ValueError(
                f"Unknown Epoch check {key!r}: ratings are keyed by " + ", ".join(method.check_ids)
            )
        fields = dict(rating.fields)
        if method.check(key).threshold is not None:
            design = fields.pop("sample_design", None)
            drawn_by = fields.pop("drawn_by", "") or ""
            population = fields.pop("population", None)
            if rating.label not in UNASSESSED:
                if design not in SAMPLE_DESIGNS:
                    raise ValueError(
                        f"ratings.{key}.fields.sample_design must say how the audited items "
                        f"were chosen: {', '.join(SAMPLE_DESIGNS)}"
                    )
                recorded = findings.investigators.get("samples")
                sampling = Sampling(
                    strategy=str(design),
                    sample=len(recorded) if isinstance(recorded, dict) else 0,
                    population=int(population) if population not in (None, "") else None,
                    drawn_by=str(drawn_by),
                )
        rows[key] = Assessment(
            check=key,
            level=rating.label,
            notes=rating.explanation,
            evidence=list(rating.evidence),
            fields=fields,
            impact=rating.impact,
            threshold_override_reason=rating.override,
        )
    for check in method.checks:
        if check.id not in rows:
            rows[check.id] = Assessment(check=check.id, level=unfinished)
        rows[check.id].validate_against(check, method)
    return rows, sampling


def _sum_states(expr: str, counts: dict[str, Any]) -> int:
    return sum(int(counts.get(term.strip(), 0)) for term in expr.split("+"))


def measures(
    method: Methodology,
    assessments: dict[str, Assessment],
    coverage: dict[str, Any] | None,
    sampling: Sampling | None,
) -> dict[str, dict[str, Any]]:
    """Each thresholded check's measure from the sample labels.

    Coverage counts measure prevalence only when the sample was selected for it; a
    purposive sample establishes that a defect exists, not how common it is.
    """
    out: dict[str, dict[str, Any]] = {}
    spec = method.sampling.measure
    for check in method.thresholded:
        assert check.threshold is not None
        threshold = check.threshold.bound
        if coverage is not None and (sampling is None or sampling.for_prevalence):
            counts = coverage.get("counts") or {}
            num = _sum_states(spec.numerator, counts)
            den = _sum_states(spec.denominator, counts)
            beside: dict[str, Any] = {
                "unresolved": int(counts.get("UNRESOLVED", 0)),
                "not_assessed": int(counts.get("NOT_ASSESSED", 0)),
                "population": int(coverage.get("denominator") or 0),
            }
            by_check = coverage.get("counts_by_check") or {}
            if len(by_check) > 1:
                beside["by_check"] = {
                    name: _sum_states(spec.numerator, states) for name, states in by_check.items()
                }
            value = num / den if den else None
            out[check.id] = {
                "name": spec.name,
                "numerator": num,
                "denominator": den,
                "value": value,
                "threshold": threshold,
                "exceeded": None if value is None else check.threshold.exceeded(value),
                "source": "the sample labels (reviews over the investigators' verdicts)",
                "beside": beside,
            }
        elif coverage is not None and sampling is not None:
            # the counts are still the page's facts (three of five chosen items defective);
            # what a purposive sample cannot support is the comparison with the threshold
            counts = coverage.get("counts") or {}
            num = _sum_states(spec.numerator, counts)
            den = _sum_states(spec.denominator, counts)
            out[check.id] = {
                "name": spec.name,
                "numerator": num,
                "denominator": den,
                "value": num / den if den else None,
                "threshold": threshold,
                "exceeded": None,
                "source": "the sample labels, on items chosen to show that defects exist",
                "inadmissible": f"a {sampling.strategy} sample does not measure prevalence",
            }
        else:
            out[check.id] = {
                "name": spec.name,
                "numerator": 0,
                "denominator": 0,
                "value": None,
                "threshold": threshold,
                "exceeded": None,
                "source": None,
                "inadmissible": "no label check ran, so there is no defect rate",
            }
    return out


# The rubric's "stop at the first Flag" rule, on by default. An operator who wants every
# row rated anyway (a pilot that always runs phase 2) turns it off in the runner's
# environment, not in the task's arguments: they are the eval-set task's identity, and
# changing them on a resume would restart a checkpointed audit from scratch.
STOP_AT_FIRST_FLAG = "INSPECT_AUDIT_EPOCH_STOP_AT_FIRST_FLAG"


def stops_at_first_flag() -> bool:
    """Whether the rubric stops at the first Flag: unless the environment says false."""
    value = os.environ.get(STOP_AT_FIRST_FLAG, "").strip().lower()
    if value in ("", "1", "true", "yes"):
        return True
    if value in ("0", "false", "no"):
        return False
    raise ValueError(f"{STOP_AT_FIRST_FLAG} must be true or false, not {value!r}")


def derive_verdict(
    method: Methodology,
    assessments: dict[str, Assessment],
    measured: dict[str, dict[str, Any]],
    *,
    stop_at_first_flag: bool = True,
) -> dict[str, Any]:
    """Apply the methodology's verdict procedure to the recorded assessments.

    Raises when a thresholded check's level contradicts its measure without a written
    reason, and when a row is rated after the rubric's stop, so the published verdict
    and its numbers cannot disagree silently. With `stop_at_first_flag` false, rows
    after the first Flag may be rated; the verdict is the same (Flawed) and its reasons
    say the rubric was not stopped.
    """
    proc = method.verdict.procedure
    fail_at = proc.minimum["fail_at"]
    reasons: list[str] = []
    for check_id, m in measured.items():
        row = assessments[check_id]
        if m["exceeded"] is None or not row.assessed:
            continue
        failing = row.level == fail_at
        if failing != m["exceeded"] and not row.threshold_override_reason.strip():
            raise ValueError(
                f"ratings.{check_id} is {row.level} but {m['numerator']}/{m['denominator']} "
                f"audited ({m['value']:.0%}) is {'at or above' if m['exceeded'] else 'below'} "
                f"the {m['threshold']:.0%} threshold; set `override` on the rating with the "
                "reason, or change the label"
            )
        if failing != m["exceeded"]:
            reasons.append(f"{check_id} threshold overridden: {row.threshold_override_reason}")

    gate = assessments[method.gate.id]
    unfinished = proc.unfinished["level"]
    if gate.level == proc.gate["stop_at"]:
        verdict = proc.gate["outcome"]
        reasons.insert(0, f"{method.gate.id} is {gate.level}: not enough information to review")
        rated = [
            c.id for c in method.checks if c.id != method.gate.id and assessments[c.id].assessed
        ]
        if rated:
            raise ValueError(
                f"the review stops at reviewability {gate.level}: {', '.join(rated)} must be "
                f"{unfinished}, not rated"
            )
    elif gate.level == unfinished:
        verdict = proc.unfinished["outcome"]
        reasons.insert(0, f"{method.gate.id} has not been assessed")
    else:
        order = method.minimum
        first = next((i for i, c in enumerate(order) if assessments[c.id].level == fail_at), None)
        if first is not None:
            after = [
                c.id for c in order[first + 1 :] + method.quality if assessments[c.id].assessed
            ]
            if after and stop_at_first_flag:
                raise ValueError(
                    f"the review stops at the first {fail_at} ({order[first].name}): "
                    f"{', '.join(after)} must be {unfinished}, not rated"
                )
            if after:
                reasons.append(
                    f"rated after the first {fail_at} ({order[first].name}), as the operator "
                    f"asked ({STOP_AT_FIRST_FLAG}=false): {', '.join(after)}"
                )
        flagged = [c.name for c in order if assessments[c.id].level == fail_at]
        pending = [c.name for c in order if assessments[c.id].level == unfinished]
        unmeasured = [
            (c.name, measured[c.id]["inadmissible"])
            for c in order
            if c.id in measured
            and measured[c.id].get("inadmissible")
            and assessments[c.id].level not in (fail_at, unfinished)
        ]
        if flagged:
            verdict = proc.minimum["outcome"]
            reasons.insert(0, f"{fail_at.lower()}ged: " + ", ".join(flagged))
        elif pending or unmeasured:
            verdict = proc.unfinished["outcome"]
            if pending:
                reasons.insert(0, "minimum standard not reviewed: " + ", ".join(pending))
            for name, why in unmeasured:
                reasons.insert(
                    0,
                    f"{name} passes without an admissible measure ({why}); record a random, "
                    f"stratified or census sample design, or {fail_at} it on a measured impact",
                )
        else:
            verdict = proc.otherwise
            reasons.insert(0, f"{method.gate.id} {gate.level}; every minimum-standard check passes")
    for check_id, m in measured.items():
        if m["value"] is None:
            continue
        if m.get("inadmissible"):
            reasons.append(
                f"{check_id}: {m['numerator']}/{m['denominator']} chosen items defective "
                f"({m['value']:.1%}); {m['inadmissible']}, so the {m['threshold']:.0%} "
                "threshold is not applied"
            )
        else:
            reasons.append(
                f"{check_id} {m['name']} {m['numerator']}/{m['denominator']} "
                f"({m['value']:.1%}; threshold {m['threshold']:.0%})"
            )
    return {"verdict": verdict, "reasons": reasons, "measures": measured, "methodology": method.key}


def phase_rule(method: Methodology, assessments: dict[str, Assessment], with_logs: bool) -> None:
    """Phase 1 (no logs) leaves every log-dependent row unassessed."""
    if with_logs:
        return
    rated = [c.id for c in method.checks if c.needs_logs and assessments[c.id].assessed]
    if rated:
        raise ValueError(
            "no evaluation logs were supplied, so this audit is phase 1 only, the task "
            f"audit: the checks answered from logs stay "
            f"{method.verdict.procedure.unfinished['level']}: " + ", ".join(rated) + ". An "
            "audit with the logs declared answers them, in the same run, after phase 1."
        )


def validate(findings: "Findings", stop_at_first_flag: bool | None = None) -> dict[str, Any]:
    """The review a bundle holds, derived; raises with the first problem found.

    `stop_at_first_flag` None reads the environment (`stops_at_first_flag()`).
    """
    if stop_at_first_flag is None:
        stop_at_first_flag = stops_at_first_flag()
    method = methodology()
    assessments, sampling = assessments_from(findings, method)
    with_logs = logs_supplied(findings)
    phase_rule(method, assessments, with_logs)
    coverage = coverage_from(findings)
    measured = measures(method, assessments, coverage, sampling)
    derived = derive_verdict(method, assessments, measured, stop_at_first_flag=stop_at_first_flag)
    derived["logs_supplied"] = with_logs
    derived["sampling"] = sampling.model_dump() if sampling else None
    derived["coverage"] = coverage
    return derived


# ------------------------------------------------------------------ the page


class Profile(BaseModel):
    """What the page's header says about the subject, written with the prose."""

    model_config = ConfigDict(extra="forbid")
    benchmark: str = Field(min_length=1)
    creator: str = Field(min_length=1)
    review_date: str = ""

    @model_validator(mode="after")
    def _dated(self) -> "Profile":
        if self.review_date:
            try:
                date.fromisoformat(self.review_date)
            except ValueError as ex:
                raise ValueError(f"review_date must be an ISO date (YYYY-MM-DD): {ex}") from ex
        return self


FOOTNOTE_MARK = re.compile(r"\[\^(\d+)\]")


def footnote_marks(text: str) -> list[int]:
    return [int(n) for n in FOOTNOTE_MARK.findall(text)]


class Prose(BaseModel):
    """The template's prose blocks, keyed by block id, with the profile and footnotes."""

    model_config = ConfigDict(extra="allow")
    profile: Profile

    def block(self, id: str) -> Any:
        return (self.model_extra or {}).get(id)

    def present(self, block: Block) -> bool:
        value = self.block(block.id)
        if value is None:
            return False
        if isinstance(value, str):
            return bool(value.strip())
        return bool(value)

    def footnote_list(self) -> list[str]:
        value = (self.model_extra or {}).get("footnotes")
        return list(value) if isinstance(value, list) else []

    def marks(self) -> set[int]:
        found: set[int] = set()
        for key, value in (self.model_extra or {}).items():
            if key == "footnotes":
                continue
            if isinstance(value, str):
                found |= set(footnote_marks(value))
            elif isinstance(value, list):
                for item in value:
                    cells = item.values() if isinstance(item, dict) else [item]
                    for cell in cells:
                        found |= set(footnote_marks(str(cell)))
        return found

    def validate_against(self, page: Template, verdict: str) -> None:
        known = {b.id for b in page.blocks} | ({"footnotes"} if page.footnotes else set())
        if unknown := set(self.model_extra or {}) - known:
            raise ValueError(
                f"prose.json: unknown blocks {', '.join(sorted(unknown))}; the template "
                f"declares {', '.join(sorted(known))} beside profile"
            )
        missing = [
            b.id
            for b in page.blocks
            if b.required_for(verdict) and page.renders(b) and not self.present(b)
        ]
        if missing:
            raise ValueError(f"a {verdict} review needs the {', '.join(missing)} block(s)")
        if page.footnotes:
            notes = (self.model_extra or {}).get("footnotes")
            if notes is not None and (
                not isinstance(notes, list) or not all(isinstance(n, str) for n in notes)
            ):
                raise ValueError("footnotes: a list of strings")
            notes = self.footnote_list()
            if len(notes) > page.footnotes.max_items:
                raise ValueError(f"footnotes: {len(notes)} notes, limit {page.footnotes.max_items}")
            for i, note in enumerate(notes, 1):
                if not note.strip():
                    raise ValueError(f"footnote {i} is empty")
                check_length(
                    f"footnote {i}", note, max_words=page.footnotes.max_words, max_sentences=None
                )
        for block in page.blocks:
            value = self.block(block.id)
            present = self.present(block)
            if present and not block.shown_for(verdict):
                raise ValueError(
                    f"{block.id} is not part of a {verdict} review; move it into the rubric notes"
                )
            if not present:
                continue
            if block.kind == "prose":
                if not isinstance(value, str):
                    raise ValueError(f"{block.id}: a prose block is a string")
                check_length(block.id, value, max_words=block.max_words, max_sentences=None)
                if block.max_paragraphs and len(paragraphs(value)) > block.max_paragraphs:
                    raise ValueError(
                        f"{block.id}: {len(paragraphs(value))} paragraphs, limit {block.max_paragraphs}"
                    )
                for i, para in enumerate(paragraphs(value), 1):
                    if (
                        block.max_sentences_per_paragraph
                        and sentences(para) > block.max_sentences_per_paragraph
                    ):
                        raise ValueError(
                            f"{block.id}: paragraph {i} has {sentences(para)} sentences, "
                            f"limit {block.max_sentences_per_paragraph}"
                        )
            elif block.kind == "bullets":
                if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
                    raise ValueError(f"{block.id}: a bullets block is a list of strings")
                if block.max_items is not None and len(value) > block.max_items:
                    raise ValueError(f"{block.id}: {len(value)} bullets, limit {block.max_items}")
                for i, item in enumerate(value, 1):
                    if not item.strip():
                        raise ValueError(f"{block.id}: bullet {i} is empty")
                    check_length(
                        f"{block.id} bullet {i}",
                        item,
                        max_words=None,
                        max_sentences=block.max_sentences,
                    )
                    if block.plain:
                        check_about_the_benchmark(
                            f"{block.id} bullet {i}", item, BULLET_PROCESS_WORDS
                        )
            elif block.kind == "table":
                if not isinstance(value, list) or not all(isinstance(v, dict) for v in value):
                    raise ValueError(f"{block.id}: a table block is a list of rows")
                keys = [c.key for c in block.columns]
                for i, row in enumerate(value, 1):
                    if set(row) != set(keys):
                        raise ValueError(f"{block.id} row {i}: columns are {', '.join(keys)}")
                    for key in keys:
                        cell = str(row[key])
                        if not cell.strip():
                            raise ValueError(f"{block.id} row {i}: {key} is empty")
                        if block.max_cell_words and words(cell) > block.max_cell_words:
                            raise ValueError(
                                f"{block.id} row {i}: {key} over {block.max_cell_words} words"
                            )
                        column = block.column(key)
                        if column.plain:
                            check_plain(
                                f"{block.id} row {i}: {key}",
                                cell,
                                max_words=column.max_words or block.max_cell_words or 25,
                            )
                        elif column.max_words or column.max_sentences:
                            check_length(
                                f"{block.id} row {i}: {key}",
                                cell,
                                max_words=column.max_words,
                                max_sentences=column.max_sentences,
                            )


def check_prose(
    prose: Prose,
    page: Template,
    verdict: str,
    coverage: dict[str, Any] | None,
    assessments: dict[str, Assessment],
) -> None:
    prose.validate_against(page, verdict)
    if has_defects(coverage):
        missing = [
            b.id
            for b in page.blocks
            if b.required_when_defects
            and b.shown_for(verdict)
            and page.renders(b)
            and not prose.present(b)
        ]
        if missing:
            raise ValueError(
                f"the sample records a defect, so the page needs the {', '.join(missing)} "
                "block(s) whatever the verdict"
            )
    if page.footnotes:
        notes = prose.footnote_list()
        marks = prose.marks()
        for row in assessments.values():
            marks |= set(footnote_marks(row.notes))
        if dangling := sorted(n for n in marks if not 1 <= n <= len(notes)):
            raise ValueError(
                f"the page refers to footnote(s) {', '.join(map(str, dangling))} but "
                f"{len(notes)} are written"
            )
        if unused := sorted(set(range(1, len(notes) + 1)) - marks):
            raise ValueError(
                f"footnote(s) {', '.join(map(str, unused))} are written but nothing refers to them"
            )
    for block in page.blocks:
        if block.footer != "coverage_ids" or not prose.present(block):
            continue
        named = " ".join(str(row.get("task", "")) for row in prose.block(block.id))
        missing = [qid for qid in coverage_ids(coverage, "DEFECT") if qid not in named]
        if missing:
            raise ValueError(
                f"{block.id} must carry one row for every item labelled a defect, naming it "
                f"in the task cell; missing {len(missing)}: " + ", ".join(missing)
            )


def _run_words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9][a-z0-9'\u2019.,-]*", text.lower())


def shared_run(a: str, b: str) -> str:
    """The longest run of consecutive words two cells share."""
    x, y = _run_words(a), _run_words(b)
    best: tuple[int, int] = (0, 0)
    lengths = [0] * (len(y) + 1)
    for i in range(1, len(x) + 1):
        previous = 0
        for j in range(1, len(y) + 1):
            current = lengths[j]
            lengths[j] = previous + 1 if x[i - 1].strip(".,") == y[j - 1].strip(".,") else 0
            if lengths[j] > best[0]:
                best = (lengths[j], i)
            previous = current
    return " ".join(x[best[1] - best[0] : best[1]])


# a run of this many words shared between two rows' cells is a row copied or edited
# into the wrong place more often than a turn of phrase
SHARED_RUN = 6


def prose_warnings(prose: Prose, page: Template) -> list[str]:
    """What the preview points out without refusing: rows that repeat another row's words.

    An auditor revising prose.json by list position can write one item's text into its
    neighbour's row (an Affected logs cell describing the row above). The page cannot
    tell a copy from a shared phrase, so it says so and leaves the call to the writer.
    """
    out: list[str] = []
    for block in page.blocks:
        value = prose.block(block.id)
        if block.kind != "table" or not isinstance(value, list):
            continue
        for column in (c for c in block.columns if c.distinct):
            cells = [str(row.get(column.key, "")) for row in value if isinstance(row, dict)]
            for i in range(len(cells)):
                for j in range(i + 1, len(cells)):
                    run = shared_run(cells[i], cells[j])
                    if len(run.split()) >= SHARED_RUN:
                        out.append(
                            f"{block.id} rows {i + 1} and {j + 1} ({value[i].get('task', '?')}, "
                            f'{value[j].get("task", "?")}): {column.key} share "{run}"; '
                            "check each row describes its own item"
                        )
    return out


def ordinal_date(iso: str) -> str:
    d = date.fromisoformat(iso)
    suffix = "th" if 11 <= d.day % 100 <= 13 else {1: "st", 2: "nd", 3: "rd"}.get(d.day % 10, "th")
    return f"{d.day}{suffix} {d.strftime('%B %Y')}"


def _cell(text: str) -> str:
    text = text.strip()
    if not text:
        return ""
    paras = [" ".join(p.split()) for p in re.split(r"\n\s*\n|\n", text) if p.strip()]
    return "<br>".join(paras).replace("|", "\\|")


def _options(options: list[str], chosen: str | None) -> str:
    return "<br>".join(f"**{o}**" if o == chosen else o for o in options)


def _bullets(items: list[str]) -> str:
    return "<br>".join(f"• {i}" for i in items)


def _value(v: Any) -> str:
    return "___" if v is None else str(v)


def _status_options(check: Check) -> list[str]:
    form = check.form if isinstance(check.form, dict) else {}
    return list(form.get("status_options") or check.scale)


def _chosen_option(check: Check, level: str) -> str:
    options = _status_options(check)
    if level in options:
        return level
    for option in options:
        if option.startswith(level) or option.lower() == level.lower():
            return option
    return level


def _question_status(check: Check, row: Assessment) -> str:
    if isinstance(check.form, str):
        values = {k: _value(row.fields.get(k)) for k in check.fields}
        line = "<br>".join(part.format(**values) for part in check.form.split("; "))
        options = [line, *(s for s in check.scale[1:])]
        chosen = line if row.level == check.scale[0] else row.level
        return _options(options, chosen)
    return _options(_status_options(check), _chosen_option(check, row.level))


def _header_value(line: HeaderLine, profile: Profile, verdict: str) -> str:
    if line.value == "verdict":
        return verdict
    _, _, attr = line.value.partition(".")
    raw = str(getattr(profile, attr, "") or "")
    if not raw:
        return "—"
    return ordinal_date(raw) if line.format == "ordinal_date" else raw


COVERAGE_FOOTER = {
    "NO_ISSUE_FOUND": "Audited without finding a defect",
    "UNRESOLVED": "Audited, unresolved",
    "NOT_ASSESSED": "Not assessed",
}


def coverage_id_lines(coverage: dict[str, Any] | None) -> list[str]:
    lines: list[str] = []
    for status, label in COVERAGE_FOOTER.items():
        ids = coverage_ids(coverage, status)
        if ids:
            lines += [f"{label} ({len(ids)}): {', '.join(ids)}", ""]
    return lines


def _measure_line(check: Check, row: Assessment, derived: dict[str, Any], unit: str) -> str:
    measured = (derived.get("measures") or {}).get(check.id)
    if not measured or measured.get("value") is None:
        return ""
    if measured.get("inadmissible"):
        return (
            f"{check.name}: {measured['numerator']} of {measured['denominator']} inspected "
            f"{unit}s defective, on {unit}s chosen to show that defects exist rather than drawn "
            f"to measure how common they are; the {measured['threshold']:.0%} threshold is not "
            "applied."
        )
    line = (
        f"{check.name} prevalence: {measured['numerator']} of {measured['denominator']} inspected "
        f"{unit}s ({measured['value']:.0%}); threshold {measured['threshold']:.0%}."
    )
    beside = measured.get("beside") or {}
    by_check = beside.get("by_check") or {}
    if len(by_check) > 1:
        parts = [f"{n} {CHECK_PHRASES.get(name, name)}" for name, n in by_check.items()]
        line += " Of these, " + " and ".join(parts) + "; a task may count under both."
    # items no check examined at all are a drawn expansion reserve left unused, not
    # items the review failed to assess
    reserve = len(_unexamined(derived.get("coverage")))
    unresolved = int(beside.get("unresolved", 0))
    not_assessed = int(beside.get("not_assessed", 0)) - reserve
    if unresolved or not_assessed:
        line += (
            f" Unresolved {unresolved}, not assessed {not_assessed} of "
            f"{int(beside.get('population', 0)) - reserve}."
        )
    if reserve:
        noun = unit if reserve == 1 else f"{unit}s"
        line += f" A reserve of {reserve} further {noun} was drawn for expansion but not used."
    if row.threshold_override_reason.strip():
        line += f" Threshold overridden: {row.threshold_override_reason.strip()}"
    return line


def _unexamined(coverage: dict[str, Any] | None) -> list[str]:
    """The items no label check gave any verdict on: drawn, never examined."""
    return [
        str(q["question_id"])
        for q in (coverage or {}).get("questions") or []
        if q["status"] == "NOT_ASSESSED"
        and all(s == "NOT_ASSESSED" for s in (q.get("by_check") or {}).values())
    ]


def sub_reason_label(sub_reason: str) -> str:
    return sub_reason.replace("_", " ").capitalize()


def class_counts(classes: dict[str, dict[str, str]], coverage: dict[str, Any] | None) -> str:
    """The defective items per Epoch class, in Epoch's order, for the scoring row."""
    defects = coverage_ids(coverage, "DEFECT")
    if not defects or not classes:
        return ""
    parts = [
        f"{i}. {CLASS_TITLES[c]}: {sum(1 for q in defects if classes.get(q, {}).get('epoch_class') == c)}"
        for i, c in enumerate(EPOCH_CLASSES, 1)
    ]
    unclassified = sum(1 for q in defects if q not in classes)
    if unclassified:
        parts.append(f"not classified: {unclassified}")
    return "Defective tasks by class:\n" + "\n".join(parts)


def _row_ids(row: dict[str, Any], defects: list[str]) -> list[str]:
    """The defective items a table row names in its task cell."""
    task = str(row.get("task", ""))
    return [q for q in defects if re.search(rf"(?<![\w.-]){re.escape(q)}(?![\w.-])", task)]


def _source_line(source: dict[str, Any]) -> str:
    quote = " ".join(str(source["quote"]).split())
    where = str(source["url"]).strip()
    link = f"<{where}>" if re.match(r"https?://", where) else where
    marker = " (the reference answer)" if source.get("key") else ""
    return f"- **{_cell(str(source['answer']))}**{marker}: {source['source'].strip()}, “{quote}” {link}"


def _sources_section(
    rows: list[dict[str, Any]],
    coverage: dict[str, Any] | None,
    sources: dict[str, list[dict[str, Any]]],
    label: Any = None,
) -> list[str]:
    """Under the errors table: each defective item's competing answers, with their sources.

    In the table's order; the items with none recorded are named in one line. Nothing
    at all when no item records sources, as with findings written before they were.
    """
    defects = coverage_ids(coverage, "DEFECT")
    if not any(qid in sources for qid in defects):
        return []
    out = ["### Competing answers and their sources", ""]
    listed: list[str] = []
    for row in rows:
        for qid in _row_ids(row, defects):
            if qid in listed:
                continue
            listed.append(qid)
            if qid not in sources:
                continue
            kind = label(qid, row) if label else str(row.get("error_type", ""))
            out += [f"**{qid}**: {_cell(kind)}", ""]
            out += [_source_line(s) for s in sources[qid]] + [""]
    missing = [q for q in listed if q not in sources]
    missing += [q for q in defects if q not in listed and q not in sources]
    if missing:
        out += [f"No competing sources recorded ({len(missing)}): {', '.join(missing)}", ""]
    return out


def render(
    page: Template,
    method: Methodology,
    prose: Prose,
    assessments: dict[str, Assessment],
    derived: dict[str, Any],
    coverage: dict[str, Any] | None,
) -> str:
    """The page, rendered from the records so it cannot drift from them.

    A template that ends after its opening (the default page) prints the header, what
    its `opening` names (the rubric, then the representative errors with their sources)
    and the notices, with only the footnotes the page refers to. The full page prints
    the header, the other blocks the verdict shows in template order (the closing ones
    last), the disclaimer, then the opening and the notices, and every footnote.
    """
    verdict = derived["verdict"]
    profile = prose.profile
    sources: dict[str, list[dict[str, Any]]] = derived.get("sources") or {}
    classes: dict[str, dict[str, str]] = derived.get("classes") or {}
    out: list[str] = [f"# {page.title.format(benchmark=profile.benchmark)}", ""]
    for i, line in enumerate(page.header):
        trailer = "  " if i < len(page.header) - 1 else ""
        out.append(f"{line.label}: {_header_value(line, profile, verdict)}{trailer}")
    out.append("")
    footnotes: list[str] = prose.footnote_list() if page.footnotes else []

    def _table(block: Block, rows: list[dict[str, Any]], label: Any = None) -> None:
        titles = [
            "Sub-reason" if label and c.key == block.by_class else c.title for c in block.columns
        ]
        out.extend([f"| {' | '.join(titles)} |", "|" + "---|" * len(block.columns)])
        out.extend(
            "| "
            + " | ".join(
                _cell(label(None, row) if label and c.key == block.by_class else str(row[c.key]))
                for c in block.columns
            )
            + " |"
            for row in rows
        )
        out.append("")

    def _render_by_class(block: Block, value: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """The rows grouped by Epoch class, in Epoch's order; returns them in that order."""
        defects = coverage_ids(coverage, "DEFECT")

        def of(row: dict[str, Any]) -> str | None:
            found = [classes[q]["epoch_class"] for q in _row_ids(row, defects) if q in classes]
            return found[0] if found else None

        def label(qid: str | None, row: dict[str, Any]) -> str:
            ids = [qid] if qid else _row_ids(row, defects)
            named = [classes[q]["sub_reason"] for q in ids if q in classes]
            return sub_reason_label(named[0]) if named else str(row.get(block.by_class, ""))

        ordered: list[dict[str, Any]] = []
        groups = [(c, CLASS_TITLES[c], [r for r in value if of(r) == c]) for c in EPOCH_CLASSES]
        rest = [r for r in value if of(r) is None]
        for i, (_, title, rows) in enumerate(groups, 1):
            out.extend([f"### {i}. {title} ({len(rows)})", ""])
            if rows:
                _table(block, rows, label)
                ordered += rows
            else:
                out.extend(["None among the inspected tasks.", ""])
        if rest:
            out.extend([f"### Not classified ({len(rest)})", ""])
            _table(block, rest, label)
            ordered += rest
        out.extend(coverage_id_lines(coverage) if block.footer == "coverage_ids" else [])
        if block.sources:
            out.extend(_sources_section(ordered, coverage, sources, label))
        return ordered

    def _render_block(block: Block) -> None:
        value = prose.block(block.id)
        if block.kind == "table" and block.by_class and classes:
            _render_by_class(block, value)
            return
        if block.kind == "table":
            out.extend(
                [
                    f"| {' | '.join(c.title for c in block.columns)} |",
                    "|" + "---|" * len(block.columns),
                ]
            )
            out.extend(
                "| " + " | ".join(_cell(str(row[c.key])) for c in block.columns) + " |"
                for row in value
            )
            out.append("")
            if block.footer == "coverage_ids":
                out.extend(coverage_id_lines(coverage))
            if block.sources:
                out.extend(_sources_section(value, coverage, sources))
            return
        if block.title:
            out.extend([f"## {block.title}", ""])
        lead = block.lead_for(verdict)
        if lead:
            out.extend([lead, ""])
        if block.kind == "prose":
            out.extend([str(value).strip(), ""])
        else:
            out.extend([f"- {b.strip()}" for b in value] + [""])

    def _render_rubric() -> None:
        rub = page.rubric
        out.extend([f"## {rub.title}", ""])
        if rub.intro:
            out.extend([method.intro[rub.intro], ""])
        for sec in rub.sections:
            out.extend([f"### {sec.title}", ""])
            if sec.intro:
                out.extend([method.intro[sec.intro], ""])
            if sec.measure_line:
                for check in method.by_section(sec.section):
                    line = _measure_line(
                        check, assessments[check.id], derived, method.sampling.unit
                    )
                    if line:
                        out.extend([line, ""])
            out.extend([f"| {' | '.join(sec.columns)} |", "|" + "---|" * len(sec.columns)])
            for check in method.by_section(sec.section):
                row = assessments[check.id]
                if sec.layout == "levels":
                    form = check.form if isinstance(check.form, dict) else {}
                    for level, spec in form.items():
                        chosen = level == row.level
                        notes = _cell(row.notes) if chosen else ""
                        status = f"**{spec['status']}**" if chosen else spec["status"]
                        out.append(
                            f"| {f'**{level}**' if chosen else level} | {spec['meaning']} | "
                            f"{status} | {notes} |"
                        )
                elif sec.layout == "defect_classes":
                    form = check.form if isinstance(check.form, dict) else {}
                    failing = row.level == method.verdict.procedure.minimum["fail_at"]
                    notes = row.notes
                    if failing and row.impact.strip() and page.footnotes:
                        footnotes.append(f"Impact: {row.impact.strip()}")
                        notes = f"{row.notes.strip()}[^{len(footnotes)}]"
                    elif failing and row.impact.strip():
                        notes = f"{row.notes.strip()} Impact: {row.impact.strip()}"
                    # the defective tasks per Epoch class, on the row the labels decide
                    counts = class_counts(classes, coverage) if check.threshold else ""
                    if counts:
                        notes = f"{notes.strip()}\n{counts}"
                    out.append(
                        f"| {check.name} | {_bullets(list(form.get('examples') or []))} | "
                        f"{form.get('default_threshold', '')} | "
                        f"{_options(_status_options(check), _chosen_option(check, row.level))} | "
                        f"{_cell(notes)} |"
                    )
                else:
                    out.append(
                        f"| {check.question} | {_question_status(check, row)} | {_cell(row.notes)} |"
                    )
            out.append("")

    def _shown(block: Block) -> bool:
        return block.shown_for(verdict) and prose.present(block)

    def _quote(text: str, max_words: int) -> list[str]:
        if "\n" in text.strip():
            lines = text.strip().splitlines()[:8]
            return ["```", *lines, "```", ""]
        return [f"> {_clip(text, max_words)}", ""]

    def _render_examined() -> None:
        """The fill: the other items examined, under the confirmed defects."""
        spec, items = page.examined, derived.get("examined") or []
        if spec is None or not items:
            return
        errors = (
            page.block("representative_errors")
            if any(b.id == "representative_errors" for b in page.blocks)
            else None
        )
        if errors is not None and not _shown(errors):
            out.extend([f"## {errors.title}", ""])
            if spec.none_lead and not coverage_ids(coverage, "DEFECT"):
                out.extend([spec.none_lead, ""])
        out.extend([f"### {spec.title} ({len(items)})", ""])
        if spec.lead:
            out.extend([spec.lead, ""])
        for item in items:
            label = spec.labels.get(item["standing"], item["standing"])
            out.extend([f"**{item['question_id']}**: {label}", ""])
            for part in item["checks"]:
                what = spec.checks.get(part["check"], part["check"])
                if part["raised"]:
                    out.extend(
                        [f"*{what}.* Raised: {_clip(part['raised'], spec.max_text_words)}", ""]
                    )
                    out.extend([f"Standing: {_clip(part['finding'], spec.max_text_words)}", ""])
                else:
                    out.extend([f"*{what}.* {_clip(part['finding'], spec.max_text_words)}", ""])
                for quote in part["quotes"]:
                    out.extend(_quote(quote, spec.max_quote_words))

    def _render_opening() -> None:
        for item in page.opening:
            if item == "rubric":
                _render_rubric()
            elif _shown(block := page.block(item)):
                _render_block(block)
        _render_examined()
        for notice in derived.get("notices") or []:
            out.extend([f"*{notice}*", ""])

    if page.ends_after_opening:
        _render_opening()
        cited = set(footnote_marks("\n".join(out)))
        notes = [(i, n) for i, n in enumerate(footnotes, 1) if i in cited]
        if notes and page.footnotes:
            out += [f"## {page.footnotes.title}", ""]
            out += [f"[^{i}]: {note.strip()}" for i, note in notes]
            out.append("")
        return "\n".join(out)
    # the full page: the prose blocks first, the disclaimer, then the opening (the
    # rubric and the representative errors with their sources), the notices, footnotes
    rest = [b for b in page.blocks if b.id not in page.opening and _shown(b)]
    for block in [b for b in rest if not b.closing] + [b for b in rest if b.closing]:
        _render_block(block)
    rub = page.rubric
    if rub.disclaimer:
        out += [
            "## Disclaimer",
            "",
            method.intro[rub.disclaimer].format(creator=profile.creator),
            "",
        ]
    if "rubric" not in page.opening:
        _render_rubric()
    _render_opening()
    if footnotes and page.footnotes:
        out += [f"## {page.footnotes.title}", ""]
        out += [f"[^{i}]: {note.strip()}" for i, note in enumerate(footnotes, 1)]
        out.append("")
    return "\n".join(out)


def review(findings: "Findings", prose: Prose, full_page: bool = False) -> dict[str, Any]:
    """Validate the bundle and the prose together, derive the verdict, render the page."""
    method, page = methodology(), template(full_page)
    derived = validate(findings)
    assessments, _ = assessments_from(findings, method)
    notices: list[str] = []
    if not derived["logs_supplied"] and "no_logs" in page.notices:
        notices.append(page.notices["no_logs"])
    provenance = findings.subject.get("logs_provenance")
    if derived["logs_supplied"] and isinstance(provenance, list) and provenance:
        notices.append(
            "The supplied logs were recorded by another build of the benchmark than the "
            "one audited here: "
            + "; ".join(str(p) for p in provenance)
            + ". Grades reproduced in this review were computed by the installed build."
        )
    derived["notices"] = notices
    if not prose.profile.review_date:
        prose.profile.review_date = datetime.now(UTC).date().isoformat()
    check_prose(prose, page, derived["verdict"], derived["coverage"], assessments)
    derived["warnings"] = prose_warnings(prose, page)
    derived["sources"] = sources_from(findings)
    derived["classes"] = classes_from(findings, derived["coverage"])
    derived["examined"] = (
        examined_from(
            findings,
            derived["coverage"],
            page.examined.fill_to - len(coverage_ids(derived["coverage"], "DEFECT")),
        )
        if page.examined
        else []
    )
    derived["template"] = page.id
    derived["assessments"] = {k: v.model_dump(mode="json") for k, v in assessments.items()}
    derived["page"] = render(page, method, prose, assessments, derived, derived["coverage"])
    return derived
