"""The scoring row's sampled defect rate, from the item verdicts in the submitted findings.

Read as the register reads them: for each item and scoring check, the take that stands is
the auditor's latest review, else the investigators' latest verdict. An item is a DEFECT
when a standing verdict is a grade its check flags; NO_ISSUE_FOUND when it has standing
verdicts, all settled and unflagged; UNRESOLVED when a verdict is unsettled and none is a
defect; NOT_ASSESSED when no scoring check reached it. The rate is DEFECT over DEFECT plus
NO_ISSUE_FOUND (Epoch's sampling.measure); the other two are reported beside it.
"""

from dataclasses import dataclass, field
from typing import Any

from inspect_audit.package import Findings


@dataclass
class DefectRate:
    checks: list[str]
    states: dict[str, list[str]] = field(default_factory=dict)
    """Item ids by state: DEFECT, NO_ISSUE_FOUND, UNRESOLVED, NOT_ASSESSED."""
    why: dict[str, list[str]] = field(default_factory=dict)
    """For each defect, the `check: GRADE` verdicts that make it one."""

    @property
    def numerator(self) -> int:
        return len(self.states["DEFECT"])

    @property
    def denominator(self) -> int:
        return self.numerator + len(self.states["NO_ISSUE_FOUND"])

    @property
    def value(self) -> float | None:
        return self.numerator / self.denominator if self.denominator else None

    def line(self) -> str:
        if self.value is None:
            return "Defect rate: no item settled by a scoring check"
        return (
            f"Defect rate: {self.numerator}/{self.denominator} ({self.value:.0%}); "
            f"{len(self.states['UNRESOLVED'])} unresolved and "
            f"{len(self.states['NOT_ASSESSED'])} not assessed beside it"
        )


def _standing(findings: Findings, sample: str, check: str) -> str | None:
    reviews = [
        r.label for r in findings.auditor.reviews if r.sample_id == sample and r.check == check
    ]
    if reviews:
        return reviews[-1]
    samples: dict[str, Any] = findings.investigators.get("samples") or {}  # type: ignore[assignment]
    takes = (samples.get(sample) or {}).get(check) or []
    return str(takes[-1]["label"]) if takes else None


def defect_rate(findings: Findings, checks: list[str]) -> DefectRate:
    definitions: dict[str, Any] = findings.investigators.get("checks") or {}  # type: ignore[assignment]
    samples: dict[str, Any] = findings.investigators.get("samples") or {}  # type: ignore[assignment]
    scoring = [c for c in checks if c in definitions]
    rate = DefectRate(
        checks=scoring,
        states={s: [] for s in ("DEFECT", "NO_ISSUE_FOUND", "UNRESOLVED", "NOT_ASSESSED")},
    )
    for sample in samples:
        labels = {c: _standing(findings, sample, c) for c in scoring}
        labels = {c: v for c, v in labels.items() if v is not None}
        flagged = [f"{c}: {v}" for c, v in labels.items() if v in definitions[c].get("flags", [])]
        unsettled = [
            c
            for c, v in labels.items()
            if v not in definitions[c].get("grades", [])
            or v in definitions[c].get("unresolved", [])
        ]
        if flagged:
            rate.states["DEFECT"].append(sample)
            rate.why[sample] = flagged
        elif not labels:
            rate.states["NOT_ASSESSED"].append(sample)
        elif unsettled:
            rate.states["UNRESOLVED"].append(sample)
        else:
            rate.states["NO_ISSUE_FOUND"].append(sample)
    return rate
