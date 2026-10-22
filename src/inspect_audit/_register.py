"""The investigation's register: every take on every item, in one machine-readable file.

The population is the benchmark's own items (numbered as an eval numbers them) crossed
with the checks the investigation declares, so missing work is visible as an empty
cell. A take is one judgment by one party: an auditor's verdict, imported from its
audit log, or the investigator's own review. Takes are only ever appended: a review
never overwrites a verdict, so a disagreement stays on the record. Broader claims
(`findings`) and per-dimension judgments (`ratings`) sit beside the grid.

The register lives in the sample store, which Inspect's checkpointing carries, and is
mirrored to /workspace/register.json after every change for the agent to read and
script against. It is the investigation's working record; reports are built from it
as a separate step.
"""

import json
from datetime import UTC, datetime
from typing import Any, Literal

from inspect_ai.util import StoreModel, sandbox, store_as
from pydantic import BaseModel, Field, JsonValue

from ._verdicts import AuditItemSkill, Evidence

BOX_PATH = "/workspace/register.json"

# the framework's scale for a dimension, as the report uses it
RATINGS = ("None", "Minor", "Major", "Critical", "Not assessed", "Not applicable")


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


class Claim(BaseModel):
    """A finding broader than one item: what the investigator concludes, and on what."""

    by: Literal["investigator"] = "investigator"
    claim: str
    explanation: str
    samples: list[str] = Field(default_factory=list)
    evidence: list[Evidence] = Field(default_factory=list)
    at: str = Field(default_factory=now)


class Register(BaseModel):
    subject: dict[str, JsonValue] = Field(default_factory=dict)
    checks: list[str] = Field(default_factory=list)
    samples: dict[str, dict[str, list[Take]]] = Field(default_factory=dict)
    findings: list[Claim] = Field(default_factory=list)
    ratings: dict[str, list[Take]] = Field(default_factory=dict)


class RegisterStore(StoreModel):
    """The register as the sample store holds it; checkpointed with the sample."""

    data: dict[str, Any] = Field(default_factory=dict)


def load() -> Register:
    return Register.model_validate(store_as(RegisterStore).data or {})


async def save(register: Register) -> None:
    """Keep the store's copy, and mirror it into the box for the agent to read."""
    from ._findings import FindingsState, require_investigation, sync_draft

    if store_as(FindingsState).enabled:
        require_investigation()
        await sync_draft(register)
    data = register.model_dump(mode="json", exclude_none=True)
    store_as(RegisterStore).data = data  # assigned, not mutated: the store sees it
    if not store_as(FindingsState).enabled:
        await sandbox().write_file(BOX_PATH, json.dumps(data, indent=1))


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

    def state(name: str, takes: list[Take]) -> set[str]:
        skill = checks.get(name)
        found: set[str] = set()
        if not takes:
            return {"missing"}
        top = standing(takes)
        if skill and top and top.label in skill.unresolved:
            found.add("unresolved")
        last_review = max(
            (i for i, take in enumerate(takes) if take.by == "investigator"), default=-1
        )
        if skill and any(t.label in skill.flags for t in takes[last_review + 1 :]):
            found.add("flagged")
        if len({t.label for t in takes}) > 1:
            found.add("disputed")
        return found

    if view == "summary":
        counts: dict[str, dict[str, int]] = {}
        for _, name, takes in cells:
            row = counts.setdefault(
                name, {"cells": 0, "missing": 0, "flagged": 0, "unresolved": 0, "disputed": 0}
            )
            row["cells"] += 1
            for found in state(name, takes):
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
        if view in state(name, takes)
    ]
