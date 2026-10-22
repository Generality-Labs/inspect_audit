"""Host-side auditor collection and shared interpretation of sample/check results.

The register retains all collected auditor takes in Inspect's checkpointed store.
The agent works in findings.json; investigator reviews are applied to a temporary
register for queries and publication, never written into the collection history.
"""

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Literal

from inspect_ai.util import StoreModel, store_as
from pydantic import BaseModel, Field, JsonValue

from ._verdicts import AuditItemSkill, Evidence

if TYPE_CHECKING:
    from ._findings import Review


def now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class Take(BaseModel):
    """One party's judgment on one thing, with why and from where."""

    by: Literal["auditor", "investigator"]
    label: str
    explanation: str
    evidence: list[Evidence] = Field(default_factory=list)
    run: str | None = None
    """The job's address, for an auditor's take."""
    eval_id: str | None = None
    epoch: int | None = None
    model: str | None = None
    record: dict[str, JsonValue] | None = None
    """The verdict exactly as the auditor recorded it."""
    at: str = Field(default_factory=now)


class Register(BaseModel):
    subject: dict[str, JsonValue] = Field(default_factory=dict)
    checks: list[str] = Field(default_factory=list)
    samples: dict[str, dict[str, list[Take]]] = Field(default_factory=dict)


class RegisterStore(StoreModel):
    """The register as the sample store holds it; checkpointed with the sample."""

    data: dict[str, Any] = Field(default_factory=dict)


def load() -> Register:
    return Register.model_validate(store_as(RegisterStore).data or {})


async def save(register: Register) -> None:
    """Refresh the combined draft, then save authoritative auditor history."""
    from ._findings import sync_draft

    await sync_draft(register)
    store_as(RegisterStore).data = register.model_dump(mode="json", exclude_none=True)


def started(register: Register) -> bool:
    return bool(register.checks)


def import_audit_log(
    register: Register, log: Any, run: str, checks: dict[str, AuditItemSkill]
) -> list[str]:
    """Each verdict in an audit log as an auditor take; returns what was added.

    Every (sample, check) score in the log becomes a take, NO_VERDICT included: an
    auditor that recorded nothing is a fact about the work, not a gap to hide. A
    sample outside the population or a check outside the register is refused, and a
    take already imported from the same run, eval and epoch is not added again.
    """
    added: list[str] = []
    for sample in log.samples or []:
        sample_id = str(sample.id)
        if sample_id not in register.samples:
            raise ValueError(f"audit sample {sample_id!r} is not in the register's population")
        for check, score in (sample.scores or {}).items():
            if check not in checks:
                continue
            if check not in register.checks:
                raise ValueError(f"the audit ran {check!r}, which the register does not track")
            metadata = dict(score.metadata or {})
            cell = register.samples[sample_id][check]
            # collecting a job twice must not count its verdicts twice
            if any(
                (t.run, t.eval_id, t.epoch, t.label)
                == (run, log.eval.eval_id, sample.epoch, str(score.value))
                for t in cell
            ):
                continue
            cell.append(
                Take(
                    by="auditor",
                    label=str(score.value),
                    explanation=str(score.explanation or ""),
                    evidence=[Evidence(**e) for e in metadata.get("evidence") or []],
                    run=run,
                    eval_id=log.eval.eval_id,
                    epoch=sample.epoch,
                    model=log.eval.model,
                    record=metadata,
                )
            )
            added.append(f"{sample_id}/{check}={score.value}")
    return added


def standing(takes: list[Take]) -> Take | None:
    """The take that stands: the investigator's latest, else the auditors' latest."""
    mine = [t for t in takes if t.by == "investigator"]
    return (mine or takes or [None])[-1]


def apply_reviews(
    register: Register, reviews: list["Review"], checks: dict[str, AuditItemSkill]
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
            Take(by="investigator", **review.model_dump(exclude={"sample_id", "check"}))
        )


def cell_states(takes: list[Take], skill: AuditItemSkill | None) -> set[str]:
    """Interpret a cell identically for progress queries and publication outcomes."""
    if not takes:
        return {"missing"}
    found: set[str] = set()
    top = standing(takes)
    if skill and top and (top.label not in skill.grades or top.label in skill.unresolved):
        found.add("unresolved")
    last_review = max((i for i, take in enumerate(takes) if take.by == "investigator"), default=-1)
    if skill and any(t.label in skill.flags for t in takes[last_review + 1 :]):
        found.add("flagged")
    if len({t.label for t in takes}) > 1:
        found.add("disputed")
    return found


View = Literal["summary", "flagged", "unresolved", "missing", "disputed"]


def select(
    register: Register, view: View, checks: dict[str, AuditItemSkill], check: str | None
) -> list[dict[str, Any]] | dict[str, Any]:
    """Cells of the grid by state, for the investigator to work through.

    flagged: an auditor flagged a problem and no investigator take has followed.
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
            "standing": (standing(takes) or Take(by="auditor", label="-", explanation="")).label,
        }
        for sample_id, name, takes in cells
        if view in cell_states(takes, checks.get(name))
    ]
