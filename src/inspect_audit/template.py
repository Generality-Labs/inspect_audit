"""A template renders records into a document. It owns layout, not judgement.

The template declares the header, the prose blocks (with their limits and the verdicts
they belong to) and how each rubric section is laid out. The renderer reads the
methodology for the form text and scales, the records for the content, and the derived
verdict for which blocks to show.
"""

import re
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from .methodology import Check, Methodology

if TYPE_CHECKING:
    from .records import Assessment, Profile, Prose

TEMPLATES = Path(__file__).parent / "investigation" / "templates"


class Column(BaseModel):
    model_config = ConfigDict(extra="forbid")
    key: str
    title: str
    max_words: int | None = None
    max_sentences: int | None = None
    # one sentence about the item for its reader, with no account of the audit
    plain: bool = False


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
    columns: list[Column] = Field(default_factory=list)
    when: list[str]
    required: list[str] = Field(default_factory=list)
    lead: str | dict[str, str] = ""
    # what the renderer appends under a table from the records: `coverage_ids` lists
    # the audited items that were not recorded as defects, so the table of errors is
    # read against the items that were checked and found sound
    footer: Literal["", "coverage_ids"] = ""

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


class Template(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    version: int
    methodology: str
    audience: str = ""
    style: dict[str, Any] = Field(default_factory=dict)
    title: str
    header: list[HeaderLine]
    blocks: list[Block]
    rubric: Rubric

    @model_validator(mode="after")
    def _consistent(self) -> "Template":
        ids = [b.id for b in self.blocks]
        if len(set(ids)) != len(ids):
            raise ValueError("block ids must be unique")
        default = self.style.get("max_sentences_per_paragraph")
        for block in self.blocks:
            if block.kind == "table" and not block.columns:
                raise ValueError(f"{block.id}: a table block needs columns")
            if block.kind == "prose" and block.max_sentences_per_paragraph is None and default:
                block.max_sentences_per_paragraph = int(default)
            if set(block.required) - set(block.when):
                raise ValueError(f"{block.id}: required verdicts must be among `when`")
        return self

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


def load_template(id: str = "epoch-review") -> Template:
    path = TEMPLATES / f"{id}.yaml"
    if not path.is_file():
        available = ", ".join(p.stem for p in sorted(TEMPLATES.glob("*.yaml")))
        raise FileNotFoundError(f"no template {id!r}; available: {available}")
    return Template.model_validate(yaml.safe_load(path.read_text()))


# ---------------------------------------------------------------- rendering


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


def _chosen_option(check: Check, level: str) -> str:
    """The form's spelling of a level, where the form's option list differs from the scale."""
    options = _status_options(check)
    if level in options:
        return level
    for option in options:
        if option.startswith(level) or option.lower() == level.lower():
            return option
    return level


def _status_options(check: Check) -> list[str]:
    form = check.form if isinstance(check.form, dict) else {}
    return list(form.get("status_options") or check.scale)


def _question_status(check: Check, row: "Assessment") -> str:
    """A quality row's Status cell: every option, the form's blanks filled, the choice bold."""
    if isinstance(check.form, str):
        # a fill-in line replaces the assessed level: "{floor} floor; {ceiling} ceiling"
        values = {k: _value(row.fields.get(k)) for k in check.fields}
        # the form's blanks sit on separate lines; a value may itself contain "; "
        line = "<br>".join(part.format(**values) for part in check.form.split("; "))
        options = [line, *(s for s in check.scale[1:])]
        chosen = line if row.level == check.scale[0] else row.level
        return _options(options, chosen)
    return _options(_status_options(check), _chosen_option(check, row.level))


def _header_value(line: HeaderLine, profile: "Profile", verdict: str) -> str:
    if line.value == "verdict":
        return verdict
    _, _, attr = line.value.partition(".")
    raw = str(getattr(profile, attr, "") or "")
    if not raw:
        return "—"
    if line.format == "ordinal_date":
        return ordinal_date(raw)
    return raw


COVERAGE_FOOTER = {
    "NO_ISSUE_FOUND": "Audited without finding a defect",
    "UNRESOLVED": "Audited, unresolved",
    "NOT_ASSESSED": "Not assessed",
}


def coverage_ids(coverage: dict[str, Any] | None, status: str) -> list[str]:
    """The ids `coverage.json` records at `status`, in its order."""
    if not coverage:
        return []
    return [
        str(q.get("question_id") or q.get("sample_id"))
        for q in coverage.get("questions") or []
        if isinstance(q, dict) and q.get("status") == status
    ]


def coverage_id_lines(coverage: dict[str, Any] | None) -> list[str]:
    """The lines under the errors table: every audited item that is not in it, by status."""
    lines: list[str] = []
    for status, label in COVERAGE_FOOTER.items():
        ids = coverage_ids(coverage, status)
        if ids:
            lines += [f"{label} ({len(ids)}): {', '.join(ids)}", ""]
    return lines


def render(
    template: Template,
    method: Methodology,
    profile: "Profile",
    assessments: dict[str, "Assessment"],
    prose: "Prose",
    derived: dict[str, Any],
    coverage: dict[str, Any] | None = None,
) -> str:
    """The deliverable, rendered from the records so it cannot drift from them."""
    verdict = derived["verdict"]
    out: list[str] = [f"# {template.title.format(benchmark=profile.benchmark)}", ""]
    for i, line in enumerate(template.header):
        trailer = "  " if i < len(template.header) - 1 else ""
        out.append(f"{line.label}: {_header_value(line, profile, verdict)}{trailer}")
    out.append("")

    for block in template.blocks:
        if not block.shown_for(verdict) or not prose.present(block):
            continue
        value = prose.block(block.id)
        if block.kind == "table":
            out += [
                f"| {' | '.join(c.title for c in block.columns)} |",
                "|" + "---|" * len(block.columns),
            ]
            out += [
                "| " + " | ".join(_cell(str(row[c.key])) for c in block.columns) + " |"
                for row in value
            ]
            out.append("")
            if block.footer == "coverage_ids":
                out += coverage_id_lines(coverage)
            continue
        if block.title:
            out += [f"## {block.title}", ""]
        lead = block.lead_for(verdict)
        if lead:
            out += [lead, ""]
        if block.kind == "prose":
            out += [str(value).strip(), ""]
        else:
            out += [f"- {b.strip()}" for b in value] + [""]

    rub = template.rubric
    out += [f"## {rub.title}", ""]
    if rub.intro:
        out += [method.intro[rub.intro], ""]
    for sec in rub.sections:
        out += [f"### {sec.title}", ""]
        if sec.intro:
            out += [method.intro[sec.intro], ""]
        if sec.measure_line:
            for check in method.by_section(sec.section):
                line = _measure_line(check, assessments[check.id], derived, method.sampling.unit)
                if line:
                    out += [line, ""]
        out += [f"| {' | '.join(sec.columns)} |", "|" + "---|" * len(sec.columns)]
        for check in method.by_section(sec.section):
            row = assessments[check.id]
            if sec.layout == "levels":
                form = check.form if isinstance(check.form, dict) else {}
                for level, spec in form.items():
                    chosen = level == row.level
                    notes = _cell(row.notes) if chosen else ""
                    out.append(
                        f"| {f'**{level}**' if chosen else level} | {spec['meaning']} | "
                        f"{spec['status']} | {notes} |"
                    )
            elif sec.layout == "defect_classes":
                form = check.form if isinstance(check.form, dict) else {}
                failing = row.level == method.verdict.procedure.minimum["fail_at"]
                notes = f"**Impact:** {row.impact.strip()} {row.notes}" if failing else row.notes
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
    if rub.disclaimer:
        out += [
            "## Disclaimer",
            "",
            method.intro[rub.disclaimer].format(
                creator=profile.creator or "the benchmark's creator"
            ),
            "",
        ]
    return "\n".join(out)


def _measure_line(check: Check, row: "Assessment", derived: dict[str, Any], unit: str) -> str:
    measured = (derived.get("measures") or {}).get(check.id)
    if not measured or measured.get("value") is None:
        return ""
    line = (
        f"{check.name} prevalence: {measured['numerator']} of {measured['denominator']} inspected "
        f"{unit}s ({measured['value']:.0%}); threshold {measured['threshold']:.0%}."
    )
    beside = measured.get("beside") or {}
    if beside.get("unresolved") or beside.get("not_assessed"):
        line += (
            f" Unresolved {beside.get('unresolved', 0)}, not assessed "
            f"{beside.get('not_assessed', 0)} of {beside.get('population', 0)}."
        )
    if row.threshold_override_reason:
        line += f" Threshold overridden: {row.threshold_override_reason}"
    return line
