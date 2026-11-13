"""The register: every permitted item against every check.

Investigators' verdicts are scores in their jobs' logs. Collecting a job reads them once
(the logs' sample summaries) into the sample store, which Inspect checkpoints, so the
register survives a resume with the conversation (FRICTION 113). The store holds the
population and those verdict rows; `load` builds the view from them. Auditor reviews
are laid over a loaded view for queries and publication, never stored here.
"""

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from inspect_ai.tool import Tool, ToolError, tool
from inspect_ai.util import StoreModel, sandbox, store_as
from pydantic import BaseModel, Field, JsonValue

from ._verdicts import Evidence, InvestigationItemSkill

if TYPE_CHECKING:
    from ._findings import Review
    from .dispatch.policy import LaunchPolicy


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
    """The population and every collected verdict, checkpointed with the sample."""

    subject: dict[str, Any] = Field(default_factory=dict)
    checks: list[str] = Field(default_factory=list)
    sample_ids: list[str] = Field(default_factory=list)
    verdicts: list[dict[str, Any]] = Field(default_factory=list)
    """One row per verdict read from a collected investigation log (see `verdicts_in`)."""


def population() -> Register:
    """The items and checks, every cell empty: what `load` fills with the verdicts."""
    stored = store_as(RegisterStore)
    return Register(
        subject=stored.subject,
        checks=list(stored.checks),
        samples={sample: {check: [] for check in stored.checks} for sample in stored.sample_ids},
    )


async def load() -> Register:
    """The register: the population with every collected investigator verdict."""
    register = population()
    add_takes(register, store_as(RegisterStore).verdicts)
    return register


def save(register: Register) -> None:
    """Keep the population (subject, checks, sample ids); verdicts are added by collection."""
    stored = store_as(RegisterStore)
    stored.subject = dict(register.subject)
    stored.checks = list(register.checks)
    stored.sample_ids = list(register.samples)


async def verdicts_in(files: list[Path], address: str, checks: list[str]) -> list[dict[str, Any]]:
    """Every verdict the investigation logs record, one row each.

    The score of each check on each sample, read from the logs' sample summaries.
    """
    from inspect_ai.log import read_eval_log_async, read_eval_log_sample_summaries_async

    rows: list[dict[str, Any]] = []
    for file in files:
        header = await read_eval_log_async(str(file), header_only=True)
        for sample in await read_eval_log_sample_summaries_async(str(file)):
            for check, score in (sample.scores or {}).items():
                if check not in checks:
                    continue
                rows.append(
                    {
                        "sample": str(sample.id),
                        "check": check,
                        "label": str(score.value),
                        "explanation": str(score.explanation or ""),
                        "metadata": dict(score.metadata or {}),
                        "run": address,
                        "eval_id": header.eval.eval_id,
                        "epoch": sample.epoch,
                        "model": header.eval.model,
                    }
                )
    return rows


def started(register: Register) -> bool:
    return bool(register.checks)


def add_takes(register: Register, rows: list[dict[str, Any]]) -> list[str]:
    """Investigator verdicts (`verdicts_in` rows) as takes; returns what was added.

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


async def start_register(task: str | None, policy: "LaunchPolicy") -> int:
    """Lay out the register from the launch policy; returns the number of items.

    Every permitted item of the benchmark, with an empty cell per policy check.
    """
    from inspect_ai.util import registry_create

    from ._task import numbered
    from ._verdicts import investigation_items
    from .dispatch.policy import PolicyStore, ensure_policy

    if not task:
        raise ToolError("the register needs the investigated task: the operator sets target_task")
    if started(population()):
        raise ToolError("the register is already started; it keeps one population")
    ensure_policy(policy, store_as(PolicyStore), require_existing=True)
    args = json.loads(policy.task_args_json)
    try:
        skills = investigation_items(list(policy.checks))
    except ValueError as ex:
        raise ToolError(str(ex)) from ex
    try:
        their = registry_create("task", task, **args)
    except Exception as ex:
        raise ToolError(
            f"cannot load {task} here to read its dataset ({type(ex).__name__}: {ex}). "
            "The benchmark's package must be installed beside inspect_audit."
        ) from ex
    dataset, positional = numbered(their)
    if policy.samples is not None:
        known = {str(s.id) for s in dataset}
        if set(policy.samples) - known:
            raise ToolError("launch policy selects sample IDs absent from the benchmark")
        dataset = [s for s in dataset if str(s.id) in policy.samples]
    register = Register(
        subject={
            **population().subject,
            "task": task,
            "task_args": args,
            "dataset": getattr(their.dataset, "name", None),
            "ids": "positional" if positional else "declared",
        },
        checks=[s.name for s in skills],
        samples={str(s.id): {c.name: [] for c in skills} for s in dataset},
    )
    save(register)
    return len(dataset)


@tool(name="register")
def register_view(task: str | None = None, policy: "LaunchPolicy | None" = None) -> Tool:
    async def execute(view: str, check: str | None) -> str:
        """The register: every permitted item against every check, and where each stands.

        It starts itself from the launch policy on first use. Cells fill as investigation
        jobs are collected and as you review; an empty cell is work not yet done.

        Args:
            view: "summary" (counts per check), "flagged" (an investigator flagged a
                problem you have not reviewed), "unresolved" (the standing take is
                unresolved), "missing" (no take yet) or "disputed" (takes disagree).
            check: Only this check; null for all.
        """
        from ._verdicts import investigation_items

        if view not in ("summary", "flagged", "unresolved", "missing", "disputed"):
            raise ToolError("view is summary, flagged, unresolved, missing or disputed")
        if not started(population()):
            if policy is None:
                raise ToolError("no register: this audit has no launch policy to start one from")
            await start_register(task, policy)
        current = await load()
        skills = {s.name: s for s in investigation_items(current.checks)}
        from ._findings import DRAFT, Findings

        try:
            draft = Findings.model_validate_json(await sandbox().read_file(DRAFT))
            apply_reviews(current, draft.auditor.reviews, skills)
        except (ValueError, OSError) as ex:
            raise ToolError(f"Cannot read draft reviews: {ex}") from ex
        rows = select(current, view, skills, check)  # type: ignore[arg-type]
        text = json.dumps(rows, indent=1)
        if len(text) > 12_000:
            text = text[:12_000] + f"\n... (read {DRAFT} for the rest)"
        return text

    return execute


async def collect_investigation(
    files: list[Path], address: str, task: str, policy: "LaunchPolicy"
) -> str:
    """An investigation job's verdicts into the register.

    A verdict on an item outside the population, or for a check the register does not
    track, refuses the collection and nothing is kept; collecting a job again adds nothing.
    """
    from ._verdicts import investigation_items

    if not started(population()):
        await start_register(task, policy)
    current = population()
    rows = await verdicts_in(files, address, current.checks)
    try:
        add_takes(await load(), rows)  # validated against what is already held
        added = add_takes(current, rows)
    except ValueError as ex:
        raise ToolError(f"cannot add this investigation to the register: {ex}") from ex
    stored = store_as(RegisterStore)
    held = {(r["run"], r["eval_id"], r["epoch"], r["sample"], r["check"]) for r in stored.verdicts}
    stored.verdicts = [
        *stored.verdicts,
        *(
            r
            for r in rows
            if (r["run"], r["eval_id"], r["epoch"], r["sample"], r["check"]) not in held
        ),
    ]
    skills = {s.name: s for s in investigation_items(current.checks)}
    flags = {g for s in skills.values() for g in s.flags}
    flagged = sum(1 for a in added if a.split("=")[-1] in flags)
    return f"  register: {len(added)} investigator verdict(s) added, {flagged} flagged"
