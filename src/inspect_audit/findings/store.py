"""The Store: read the current view; write review decisions, the only way they are written.

A decision names what the reviewer pointed at, is resolved to fingerprints, checked against the
current fold, written as one object under `review/`, and the derived views, summaries, parquet
and export re-rendered. Nothing shared is edited in place, so the store works the same on a
directory, S3 or R2.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from .fs import StoreFS, as_store_fs
from .io import read_current, read_runs
from .models import Finding, Run, Status
from .review import (
    AcceptPayload,
    Decision,
    LinkPayload,
    RetractPayload,
    Review,
    StatusPayload,
    SuppressionRule,
    SuppressPayload,
    apply_review,
    load_decisions,
    load_review,
    new_decision_id,
    write_decision,
)

_AUTHOR = re.compile(r"[^<>]+ <[^<>@\s]+@[^<>@\s]+>")


class StoreError(ValueError):
    """A decision that cannot be applied: nothing selected, an unknown id, or a conflict."""


@dataclass(frozen=True)
class Selection:
    """What a reviewer points at: an eval and rule, record ids, or fingerprints."""

    eval: str | None = None
    rule: str | None = None
    ids: tuple[str, ...] = ()
    fingerprints: tuple[str, ...] = ()


def _next_issue_id(review: Review) -> str:
    numbers = [int(m.group(1)) for i in review.issues if (m := re.fullmatch(r"ISS-(\d+)", i.id))]
    return f"ISS-{max(numbers, default=0) + 1:04d}"


class Store:
    def __init__(self, locator: StoreFS | str | Path) -> None:
        self.fs = as_store_fs(locator)

    def runs(self) -> list[Run]:
        return read_current(self.fs)

    def history(self) -> list[Run]:
        return read_runs(self.fs)

    def review(self) -> Review:
        return load_review(self.fs)

    def reviewed(self) -> list[Run]:
        return apply_review(self.runs(), self.review())

    def resolve(self, selection: Selection) -> list[Finding]:
        """The findings in the current view that the selection names."""
        findings = [f for run in self.runs() for f in run.findings]
        if selection.ids:
            by_id = {f.id: f for f in findings}
            unknown = [i for i in selection.ids if i not in by_id]
            if unknown:
                raise StoreError(f"unknown record id(s): {', '.join(unknown)}")
            chosen = [by_id[i] for i in selection.ids]
        elif selection.fingerprints:
            by_fp: dict[str, Finding] = {}
            for f in findings:
                by_fp.setdefault(f.fingerprint, f)
            unknown = [fp for fp in selection.fingerprints if fp not in by_fp]
            if unknown:
                raise StoreError(
                    f"unknown fingerprint(s) in the current view: {', '.join(unknown)}"
                )
            chosen = [by_fp[fp] for fp in selection.fingerprints]
        elif selection.rule:
            chosen = [f for f in findings if f.rule == selection.rule]
        else:
            raise StoreError("a selection needs a rule, record ids or fingerprints")
        if selection.eval:
            chosen = [f for f in chosen if f.subject.eval == selection.eval]
        if not chosen:
            raise StoreError(f"nothing in the current view matches {selection}")
        return chosen

    def suppress(
        self,
        *,
        rule: str,
        author: str,
        reason: str,
        subject: str = "*",
        producer: str | None = None,
        kind: str = "false_positive",
    ) -> Decision:
        now = _now()
        probe = SuppressionRule(
            rule=rule,
            subject=subject,
            producer=producer,
            kind=kind,
            author=author,
            reason=reason,
            since=now.date(),
        )
        if not any(probe.matches(f) for run in self.runs() for f in run.findings):
            scope = f"rule {rule} on {subject}" + (f" from {producer}" if producer else "")
            raise StoreError(f"nothing in the current view matches {scope}")
        payload = SuppressPayload(rule=rule, subject=subject, producer=producer, kind=kind)
        return self._write(self._decision(now, author, reason, suppress=payload))

    def accept(
        self, selection: Selection, *, title: str, author: str, reason: str | None = None
    ) -> Decision:
        review = self.review()
        findings = self.resolve(selection)
        subjects = sorted({f.subject.eval for f in findings})
        if len(subjects) != 1:
            raise StoreError(
                f"an issue belongs to one eval; the selection spans {', '.join(subjects)}"
            )
        owner = {fp: issue.id for issue in review.issues for fp in issue.findings}
        taken = sorted(
            {
                f"{f.fingerprint} ({owner[f.fingerprint]})"
                for f in findings
                if f.fingerprint in owner
            }
        )
        if taken:
            raise StoreError(f"already accepted into another issue: {', '.join(taken)}")
        payload = AcceptPayload(
            issue=_next_issue_id(review),
            title=title,
            subject=subjects[0],
            fingerprints=sorted({f.fingerprint for f in findings}),
        )
        return self._write(self._decision(_now(), author, reason, accept=payload))

    def link(self, issue_id: str, url: str, *, author: str) -> Decision:
        if issue_id not in {issue.id for issue in self.review().issues}:
            raise StoreError(f"no issue {issue_id}")
        payload = LinkPayload(issue=issue_id, url=url)
        return self._write(self._decision(_now(), author, None, link=payload))

    def set_status(
        self, selection: Selection, *, status: Status, author: str, reason: str
    ) -> Decision:
        findings = self.resolve(selection)
        payload = StatusPayload(
            fingerprints=sorted({f.fingerprint for f in findings}), status=status
        )
        return self._write(self._decision(_now(), author, reason, status=payload))

    def retract(self, decision_id: str, *, author: str, reason: str) -> Decision:
        if decision_id not in {d.id for d in load_decisions(self.fs)}:
            raise StoreError(f"unknown decision {decision_id}")
        payload = RetractPayload(decision=decision_id)
        return self._write(self._decision(_now(), author, reason, retract=payload))

    @staticmethod
    def _decision(
        now: datetime,
        author: str,
        reason: str | None,
        *,
        suppress: SuppressPayload | None = None,
        accept: AcceptPayload | None = None,
        link: LinkPayload | None = None,
        status: StatusPayload | None = None,
        retract: RetractPayload | None = None,
    ) -> Decision:
        payloads = {
            "suppress": suppress,
            "accept": accept,
            "link": link,
            "status": status,
            "retract": retract,
        }
        (kind,) = [k for k, v in payloads.items() if v is not None]
        return Decision.model_validate(
            {
                "id": new_decision_id(now),
                "at": now,
                "author": author,
                "kind": kind,
                "reason": reason,
                kind: payloads[kind],
            }
        )

    def _write(self, decision: Decision) -> Decision:
        from .cli import render_current  # the renderer lives beside the CLI

        if not _AUTHOR.fullmatch(decision.author):
            raise StoreError(f"author must be 'Name <email>', got {decision.author!r}")
        write_decision(self.fs, decision)
        render_current(self.fs)
        return decision


def _now() -> datetime:
    return datetime.now(tz=UTC)
