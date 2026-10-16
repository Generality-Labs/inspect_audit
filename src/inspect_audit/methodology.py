"""A methodology: what to check and how to judge it, as versioned data.

The investigator's brief is a methodology (checks with ids, the question each asks,
guidance, a scale, any threshold, the sampling policy and the verdict rule) plus the
subject and the operator's steer. Templates render the records the investigator writes
against it. This module loads a methodology file and exposes it to validators and
renderers; it holds no rubric text of its own.
"""

import re
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

METHODOLOGIES = Path(__file__).parent / "investigation" / "methodologies"


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
    # tighter limits than the methodology's for this row's notes and impact
    notes_limit: dict[str, int] | None = None
    impact_limit: dict[str, int] | None = None
    threshold: Threshold | None = None
    evidence: list[str] = Field(default_factory=list)
    taxonomy: dict[str, list[str]] = Field(default_factory=dict)

    @property
    def unassessed_levels(self) -> set[str]:
        """Levels that mean the check was not answered, so need neither evidence nor fields."""
        return {s for s in self.scale if s in ("Not Reviewed", "Not reviewed", "Unknown")}


class VerdictProcedure(BaseModel):
    model_config = ConfigDict(extra="forbid")
    gate: dict[str, str]
    minimum: dict[str, str]
    unfinished: dict[str, str]
    otherwise: str


class Verdict(BaseModel):
    model_config = ConfigDict(extra="forbid")
    scale: list[str]
    rule: str
    stop: str = ""
    flag: str = ""
    procedure: VerdictProcedure
    impact: dict[str, int] = Field(default_factory=lambda: {"max_words": 60, "max_sentences": 3})


class Measure(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    numerator: str
    denominator: str
    note: str = ""


class Sampling(BaseModel):
    model_config = ConfigDict(extra="forbid")
    unit: str
    population: str
    sample: str
    expansion: str = ""
    confirmation: str = ""
    states: list[str]
    measure: Measure


class Methodology(BaseModel):
    """A versioned methodology document."""

    model_config = ConfigDict(extra="forbid")
    id: str
    version: int
    status: Literal["draft", "current", "superseded"]
    source: str = ""
    notes: str = ""
    intro: dict[str, str] = Field(default_factory=dict)
    verdict: Verdict
    sampling: Sampling
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
        seen: list[str] = []
        for c in self.checks:
            if c.section not in seen:
                seen.append(c.section)
        return seen

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


def load_methodology(key: str = "epoch-rubric@1") -> Methodology:
    """Load `<id>@<version>` from the package's methodologies directory."""
    id, _, version = key.partition("@")
    path = METHODOLOGIES / f"{id}-{version or 1}.yaml"
    if not path.is_file():
        available = ", ".join(p.stem for p in sorted(METHODOLOGIES.glob("*.yaml")))
        raise FileNotFoundError(f"no methodology {key!r}; available: {available}")
    return Methodology.model_validate(yaml.safe_load(path.read_text()))


def available_methodologies() -> list[str]:
    out = []
    for p in sorted(METHODOLOGIES.glob("*.yaml")):
        stem, _, version = p.stem.rpartition("-")
        out.append(f"{stem}@{version}")
    return out
