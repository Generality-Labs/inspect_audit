"""The register: every permitted item against every check, as a view.

Investigators' verdicts live in one place, the audit's Scout results (`_results`); the
store keeps only the population (subject, checks, sample ids) and where those results
are. `load` puts the two together whenever it is asked, so there is no second copy of
any verdict to fall out of step (FRICTION 92, 102). Auditor reviews are laid over a
loaded view for queries and publication, never written into the results.
"""

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Literal

from inspect_ai.util import StoreModel, store_as
from pydantic import BaseModel, Field, JsonValue

from ._verdicts import Evidence, InvestigationItemSkill

if TYPE_CHECKING:
    from ._findings import Review


def now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class Take(BaseModel):
    """One party's judgment on one thing, with why and from where."""

    by: Literal["investigator", "auditor"]
    label: str
    explanation: str
    evidence: list[Evidence] = Field(default_factory=list)
    run: str | None = None
    """The job's address, for an investigator's take."""
    eval_id: str | None = None
    epoch: int | None = None
    model: str | None = None
    record: dict[str, JsonValue] | None = None
    """The verdict exactly as the investigator recorded it."""
    at: str = Field(default_factory=now)


class Register(BaseModel):
    subject: dict[str, JsonValue] = Field(default_factory=dict)
    checks: list[str] = Field(default_factory=list)
    samples: dict[str, dict[str, list[Take]]] = Field(default_factory=dict)


class RegisterStore(StoreModel):
    """The population, checkpointed with the sample: never a verdict (those are results)."""

    subject: dict[str, Any] = Field(default_factory=dict)
    checks: list[str] = Field(default_factory=list)
    sample_ids: list[str] = Field(default_factory=list)
    results: str | None = None
    """The audit root whose `results/` holds the investigators' Scout results."""


def population() -> Register:
    """The items and checks, every cell empty: what `load` fills from the results."""
    stored = store_as(RegisterStore)
    return Register(
        subject=stored.subject,
        checks=list(stored.checks),
        samples={sample: {check: [] for check in stored.checks} for sample in stored.sample_ids},
    )


async def load() -> Register:
    """The register: the population with every investigator verdict in the results."""
    from pathlib import Path

    from ._results import investigator_takes

    register = population()
    results = store_as(RegisterStore).results
    if results and register.checks:
        add_takes(register, await investigator_takes(Path(results), register.checks))
    return register


def save(register: Register, results: str | None = None) -> None:
    """Keep the population (subject, checks, sample ids); verdicts are never stored here."""
    stored = store_as(RegisterStore)
    stored.subject = dict(register.subject)
    stored.checks = list(register.checks)
    stored.sample_ids = list(register.samples)
    if results is not None:
        stored.results = results


def started(register: Register) -> bool:
    return bool(register.checks)


def add_takes(register: Register, rows: list[dict[str, Any]]) -> list[str]:
    """Investigator verdicts (`_results.investigator_takes` rows) as takes; returns what was added.

    Every verdict becomes a take, NO_VERDICT included: an investigator that recorded
    nothing is a fact about the work, not a gap to hide. A sample outside the population
    or a check outside the register is refused, and a verdict from the same run, eval
    and epoch is counted once however often its job is collected.
    """
    added: list[str] = []
    for row in rows:
        sample_id, check = row["sample"], row["check"]
        if sample_id not in register.samples:
            raise ValueError(
                f"investigation sample {sample_id!r} is not in the register's population"
            )
        if check not in register.checks:
            raise ValueError(f"the investigation ran {check!r}, which the register does not track")
        cell = register.samples[sample_id][check]
        if any(
            (t.run, t.eval_id, t.epoch, t.label)
            == (row["run"], row["eval_id"], row["epoch"], row["label"])
            for t in cell
        ):
            continue
        metadata = dict(row.get("metadata") or {})
        cell.append(
            Take(
                by="investigator",
                label=row["label"],
                explanation=row["explanation"],
                evidence=[Evidence(**e) for e in metadata.get("evidence") or []],
                run=row["run"],
                eval_id=row["eval_id"],
                epoch=row["epoch"],
                model=row["model"],
                record=metadata,
            )
        )
        added.append(f"{sample_id}/{check}={row['label']}")
    return added


def standing(takes: list[Take]) -> Take | None:
    """The take that stands: the auditor's latest, else the investigators' latest."""
    mine = [t for t in takes if t.by == "auditor"]
    return (mine or takes or [None])[-1]


def apply_reviews(
    register: Register, reviews: list["Review"], checks: dict[str, InvestigationItemSkill]
) -> None:
    """Overlay draft or submitted reviews on a temporary collection snapshot."""
    for review in reviews:
        skill = checks.get(review.check)
        if (
            review.sample_id not in register.samples
            or review.check not in register.samples[review.sample_id]
            or skill is None
            or review.label not in skill.grades
        ):
            raise ValueError("Review must name an existing sample/check and a valid grade")
        register.samples[review.sample_id][review.check].append(
            Take(by="auditor", **review.model_dump(exclude={"sample_id", "check"}))
        )


def cell_states(takes: list[Take], skill: InvestigationItemSkill | None) -> set[str]:
    """Interpret a cell identically for progress queries and publication outcomes."""
    if not takes:
        return {"missing"}
    found: set[str] = set()
    top = standing(takes)
    if skill and top and (top.label not in skill.grades or top.label in skill.unresolved):
        found.add("unresolved")
    last_review = max((i for i, take in enumerate(takes) if take.by == "auditor"), default=-1)
    if skill and any(t.label in skill.flags for t in takes[last_review + 1 :]):
        found.add("flagged")
    if len({t.label for t in takes}) > 1:
        found.add("disputed")
    return found


View = Literal["summary", "flagged", "unresolved", "missing", "disputed"]


def select(
    register: Register, view: View, checks: dict[str, InvestigationItemSkill], check: str | None
) -> list[dict[str, Any]] | dict[str, Any]:
    """Cells of the grid by state, for the auditor to work through.

    flagged: an investigator flagged a problem and no auditor take has followed.
    unresolved: the standing take is unresolved. missing: no take at all.
    disputed: the takes on a cell carry more than one label.
    summary: counts of each state per check.
    """
    cells = [
        (sample_id, name, takes)
        for sample_id, by_check in register.samples.items()
        for name, takes in by_check.items()
        if check is None or name == check
    ]

    if view == "summary":
        counts: dict[str, dict[str, int]] = {}
        for _, name, takes in cells:
            row = counts.setdefault(
                name, {"cells": 0, "missing": 0, "flagged": 0, "unresolved": 0, "disputed": 0}
            )
            row["cells"] += 1
            for found in cell_states(takes, checks.get(name)):
                row[found] += 1
        return counts
    return [
        {
            "sample": sample_id,
            "check": name,
            "labels": [f"{t.by}:{t.label}" for t in takes],
            "standing": (
                standing(takes) or Take(by="investigator", label="-", explanation="")
            ).label,
        }
        for sample_id, name, takes in cells
        if view in cell_states(takes, checks.get(name))
    ]
