"""The records an investigation writes against a methodology.

A Profile of the subject, one Assessment per check, the Sampling design behind the
coverage counts, and the Prose blocks a template declares. Findings and Coverage keep
their existing modules. Every validator here reads its vocabulary from the methodology
or the template; nothing in this module names a rubric row.
"""

import re
from datetime import date
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ._report import EvidenceRef
from .methodology import Check, Methodology
from .template import Block, Template

UNASSESSED = ("Not Reviewed", "Not reviewed", "Unknown")


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


# the subset that is about the audit even in a bullet about a run: a bullet may speak of
# attempts, agents and candidates, since a run-level error is about them
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


def check_about_the_benchmark(name: str, text: str, words: frozenset[str] = PROCESS_WORDS) -> None:
    """The text says what is wrong, not how that was found out."""
    found = sorted({w for w in re.findall(r"[A-Za-z]+", text.lower()) if w in words})
    if found:
        raise ValueError(
            f"{name}: says how the audit went ({', '.join(found)}); say what is wrong with "
            "the benchmark instead, as its reader would see it"
        )


# a rubric note states a conclusion about the benchmark; which model did what belongs in
# a footnote, so a note that names one is sent there
MODEL_NAMES = re.compile(
    r"\b(gpt|claude|gemini|qwen|llama|deepseek|mistral|grok|opus|sonnet|haiku|luna|astra"
    r"|o[1-9](?:-mini|-pro)?)\b",
    re.IGNORECASE,
)


def check_no_model_names(name: str, text: str) -> None:
    found = sorted({m.group(0) for m in MODEL_NAMES.finditer(text)}, key=str.lower)
    if found:
        raise ValueError(
            f"{name}: names a model ({', '.join(found)}); a note states the conclusion, "
            "and which model did what is a footnote"
        )


def check_plain(name: str, text: str, *, max_words: int = 25) -> None:
    """One sentence, within the limit, about the item and not about the audit."""
    check_length(name, text, max_words=max_words, max_sentences=1)
    check_about_the_benchmark(name, text)


def check_length(name: str, text: str, *, max_words: int | None, max_sentences: int | None) -> None:
    if max_words is not None and words(text) > max_words:
        raise ValueError(f"{name}: {words(text)} words, limit {max_words}")
    if max_sentences is not None and sentences(text) > max_sentences:
        raise ValueError(f"{name}: {sentences(text)} sentences, limit {max_sentences}")


class Profile(BaseModel):
    """Facts about the subject every template renders from, written after orientation."""

    model_config = ConfigDict(extra="forbid")
    benchmark: str = Field(min_length=1)
    creator: str = ""
    # the measurement claim in one sentence, as the creator states it
    claim: str = ""
    # what was reviewed: version, commit, leaderboard date
    snapshot: str = ""
    population: int | None = None
    composition: dict[str, Any] = Field(default_factory=dict)
    scorer: str = ""
    grader: str = ""
    elicitation: dict[str, Any] = Field(default_factory=dict)
    harness: str = ""
    sources: dict[str, str] = Field(default_factory=dict)
    # ISO date; filled with the publication date when left empty
    review_date: str = ""

    @model_validator(mode="after")
    def _dated(self) -> "Profile":
        if self.review_date:
            try:
                date.fromisoformat(self.review_date)
            except ValueError as ex:
                raise ValueError(f"review_date must be an ISO date (YYYY-MM-DD): {ex}") from ex
        return self


class Measure(BaseModel):
    """A measured quantity behind an assessment, data rather than prose."""

    model_config = ConfigDict(extra="forbid")
    name: str
    numerator: int
    denominator: int
    threshold: float | None = None
    source: str = ""

    @property
    def value(self) -> float | None:
        return self.numerator / self.denominator if self.denominator else None

    @model_validator(mode="after")
    def _sane(self) -> "Measure":
        if self.denominator < 0 or not 0 <= self.numerator <= max(self.denominator, 0):
            raise ValueError(f"{self.name}: 0 <= numerator <= denominator")
        return self


class Assessment(BaseModel):
    """One check, one level, a short reason, the evidence and findings it rests on."""

    model_config = ConfigDict(extra="forbid")
    check: str
    level: str
    notes: str = ""
    evidence: list[EvidenceRef] = Field(default_factory=list)
    findings: list[str] = Field(default_factory=list)
    # the numbers a check's form asks for (contamination dates, score floor and ceiling)
    fields: dict[str, str | float | int | None] = Field(default_factory=dict)
    # a failing level on a check that requires it must say what the defect does to the
    # headline results, with numbers
    impact: str = ""
    measure: Measure | None = None
    # a level that disagrees with the measured threshold is allowed only with a reason
    threshold_override_reason: str | None = None

    @property
    def assessed(self) -> bool:
        return self.level not in UNASSESSED

    def validate_against(self, check: Check, method: Methodology) -> None:
        name = self.check
        if self.level not in check.scale:
            raise ValueError(
                f"{name}: level must be one of {', '.join(check.scale)}, not {self.level!r}"
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
            f"{name} notes",
            self.notes,
            max_words=limit.get("max_words"),
            max_sentences=limit.get("max_sentences"),
        )
        check_no_model_names(f"{name} notes", self.notes)
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
        if check.threshold is None and (self.measure or self.threshold_override_reason):
            raise ValueError(
                f"{name}: measures and threshold overrides belong to a check with a threshold"
            )
        if (
            self.measure and self.measure.name != check.threshold.measure
            if check.threshold
            else False
        ):
            raise ValueError(f"{name}: measure must be {check.threshold.measure}")  # type: ignore[union-attr]


def validate_assessments(rows: list[Assessment], method: Methodology) -> dict[str, Assessment]:
    """Every check assessed exactly once, each against its own check's rules."""
    ids = [r.check for r in rows]
    if sorted(ids) != sorted(method.check_ids):
        raise ValueError(
            f"assessments must contain every check of {method.key} exactly once: "
            + ", ".join(method.check_ids)
        )
    for row in rows:
        row.validate_against(method.check(row.check), method)
    return {r.check: r for r in rows}


class Sampling(BaseModel):
    """How the examined units were chosen, so a reader can weigh the coverage counts."""

    model_config = ConfigDict(extra="forbid")
    population: int
    sample: int
    # random, stratified, purposive, census
    strategy: str = Field(min_length=1)
    strata: str = ""
    seed: str = ""
    # who made the draw when it was not this investigation. A sample is random when its
    # draw was random, whoever drew it: record the design, and name the drawer here
    drawn_by: str = ""
    expansion: str = ""
    confirmation: str = ""

    @model_validator(mode="after")
    def _sane(self) -> "Sampling":
        if self.population < 0 or not 0 <= self.sample <= self.population:
            raise ValueError("sampling: 0 <= sample <= population")
        return self

    @property
    def for_prevalence(self) -> bool:
        return (
            self.strategy.lower() in ("random", "stratified", "census")
            or self.sample == self.population
        )


class TableRow(BaseModel):
    model_config = ConfigDict(extra="allow")


class Prose(BaseModel):
    """The template's prose blocks, keyed by block id; checked against the template."""

    model_config = ConfigDict(extra="allow")

    def block(self, id: str) -> Any:
        return getattr(self, id, None)

    def present(self, block: Block) -> bool:
        value = self.block(block.id)
        if value is None:
            return False
        if isinstance(value, str):
            return bool(value.strip())
        return bool(value)

    def footnote_list(self) -> list[str]:
        """The authored footnotes, an empty list when none are written."""
        value = (self.model_extra or {}).get("footnotes")
        return list(value) if isinstance(value, list) else []

    def marks(self) -> set[int]:
        """Every footnote number the prose blocks refer to."""
        from .template import footnote_marks

        found: set[int] = set()
        for key, value in self.model_dump().items():
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

    def validate_against(self, template: Template, verdict: str) -> None:
        known = {b.id for b in template.blocks} | ({"footnotes"} if template.footnotes else set())
        if unknown := set(self.model_dump()) - known:
            raise ValueError(
                f"prose: unknown blocks {', '.join(sorted(unknown))}; template declares {', '.join(sorted(known))}"
            )
        missing = [b.id for b in template.blocks if b.required_for(verdict) and not self.present(b)]
        if missing:
            raise ValueError(f"a {verdict} review needs the {', '.join(missing)} block(s)")
        if template.footnotes:
            notes = (self.model_extra or {}).get("footnotes")
            if notes is not None and (
                not isinstance(notes, list) or not all(isinstance(n, str) for n in notes)
            ):
                raise ValueError("footnotes: a list of strings")
            notes = self.footnote_list()
            if len(notes) > template.footnotes.max_items:
                raise ValueError(
                    f"footnotes: {len(notes)} notes, limit {template.footnotes.max_items}"
                )
            for i, note in enumerate(notes, 1):
                if not note.strip():
                    raise ValueError(f"footnote {i} is empty")
                check_length(
                    f"footnote {i}",
                    note,
                    max_words=template.footnotes.max_words,
                    max_sentences=None,
                )
            if dangling := sorted(n for n in self.marks() if not 1 <= n <= len(notes)):
                raise ValueError(
                    f"prose refers to footnote(s) {', '.join(map(str, dangling))} but "
                    f"{len(notes)} are written"
                )
        for block in template.blocks:
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
