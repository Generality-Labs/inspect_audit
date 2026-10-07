"""The export: the JSON the table site reads, from the reviewed current view.

Two documents: an index with every active finding across every eval and a roll-up per eval,
and one document per eval with everything its page shows. This module decides what leaves the
store: suppressed observations are counted and grouped but never rows, producers' native
`source` records are not exported, and authors are names without emails.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any

from .adapters import slug
from .models import Finding, Run
from .render import sorted_findings
from .review import Review

SCHEMA = 1
EXPORT_DIR = "export"

Seen = Mapping[str, tuple[datetime, datetime]]


def iso(dt: datetime) -> str:
    return dt.astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def display_author(author: str) -> str:
    """The name part of `Name <email>`; emails do not leave the store."""
    return author.split("<", 1)[0].strip() or author


def is_skipped(run: Run) -> bool:
    return bool(run.outcomes) and all(outcome.status == "skip" for outcome in run.outcomes)


def skip_reason(run: Run) -> str | None:
    return run.outcomes[0].message if is_skipped(run) else None


def seen_range(history: Sequence[Run]) -> dict[str, tuple[datetime, datetime]]:
    """Fingerprint to (first, last) run timestamp across every run given, usually the whole history."""
    seen: dict[str, tuple[datetime, datetime]] = {}
    for run in history:
        for finding in run.findings:
            first, last = seen.get(finding.fingerprint, (run.timestamp, run.timestamp))
            seen[finding.fingerprint] = (min(first, run.timestamp), max(last, run.timestamp))
    return seen


def _active(runs: Sequence[Run]) -> list[Finding]:
    return [f for run in runs for f in run.findings if not f.suppressions]


def _suppressed(runs: Sequence[Run]) -> list[Finding]:
    return [f for run in runs for f in run.findings if f.suppressions]


def _seen(seen: Seen, finding: Finding, run: Run) -> tuple[str, str]:
    first, last = seen.get(finding.fingerprint, (run.timestamp, run.timestamp))
    return iso(first), iso(last)


def _eval_entry(eval_name: str, runs: Sequence[Run], review: Review) -> dict[str, Any]:
    subject = runs[0].subject
    return {
        "eval": eval_name,
        "slug": slug(eval_name),
        "revision": subject.revision.model_dump(),
        "task_version": subject.task_version.full if subject.task_version else None,
        "last_run": iso(max(run.timestamp for run in runs)),
        "producers": {
            run.producer: {
                "run_id": run.id,
                "timestamp": iso(run.timestamp),
                "skipped": skip_reason(run),
            }
            for run in sorted(runs, key=lambda r: r.producer)
        },
        "active": len(_active(runs)),
        "suppressed": len(_suppressed(runs)),
        "issues": sum(1 for issue in review.issues if issue.subject == eval_name),
    }


def _finding_row(finding: Finding, run: Run, review: Review, seen: Seen) -> dict[str, Any]:
    github = next((i.github for i in review.issues if i.id == finding.issue), None)
    first, last = _seen(seen, finding, run)
    return {
        "id": finding.id,
        "fingerprint": finding.fingerprint,
        "eval": finding.subject.eval,
        "slug": slug(finding.subject.eval),
        "producer": finding.producer,
        "rule": finding.rule,
        "dimension": finding.dimension,
        "check": finding.check,
        "severity": finding.severity,
        "status": finding.status,
        "summary": finding.summary,
        "location": finding.primary_location.key(),
        "issue": finding.issue,
        "github": github,
        "first_seen": first,
        "last_seen": last,
    }


def index_document(
    runs_by_eval: Mapping[str, Sequence[Run]],
    review: Review,
    seen: Seen,
    generated_at: datetime,
) -> dict[str, Any]:
    """Every active finding across every eval, and a roll-up per eval."""
    rows: list[dict[str, Any]] = []
    for eval_name in sorted(runs_by_eval):
        for run in runs_by_eval[eval_name]:
            active = [f for f in run.findings if not f.suppressions]
            rows += [_finding_row(f, run, review, seen) for f in sorted_findings(active)]
    rows.sort(key=lambda r: (r["eval"], r["rule"], r["id"]))
    return {
        "generated_at": iso(generated_at),
        "schema": SCHEMA,
        "evals": [_eval_entry(e, runs_by_eval[e], review) for e in sorted(runs_by_eval)],
        "findings": rows,
    }
