"""The export: the JSON the table site reads, from the reviewed current view.

Two documents: an index with every active finding across every eval and a roll-up per eval,
and one document per eval with everything its page shows. This module decides what leaves the
store: suppressed observations are counted and grouped but never rows, producers' native
`source` records are not exported, and authors are names without emails.
"""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .adapters import slug
from .models import Finding, Run
from .render import SEVERITY_ORDER, inputs_data, sorted_findings
from .review import Review

SCHEMA = 1
EXPORT_DIR = "export"

Seen = Mapping[str, tuple[datetime, datetime]]


def iso(dt: datetime) -> str:
    return dt.astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def display_author(author: str) -> str:
    """The name part of `Name <email>`; emails do not leave the store.

    A bare address, which hand-written review files may hold, exports as its local part.
    """
    name = author.split("<", 1)[0].strip()
    if name and "@" not in name:
        return name
    rest = author.strip("<> ")
    return rest.split("@", 1)[0].strip("<> ") or rest


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


def _run_entry(run: Run) -> dict[str, Any]:
    return {
        "run_id": run.id,
        "producer": run.producer,
        "producer_version": run.producer_version,
        "timestamp": iso(run.timestamp),
        "duration_s": run.duration_s,
        "skipped": skip_reason(run),
        "passing": sum(1 for o in run.outcomes if o.status == "pass"),
        "outcomes": [
            {"rule": o.rule, "status": o.status, "message": o.message}
            for o in run.outcomes
            if o.status != "pass"
        ],
    }


def _group_finding(finding: Finding, run: Run, seen: Seen) -> dict[str, Any]:
    first, last = _seen(seen, finding, run)
    return {
        "id": finding.id,
        "fingerprint": finding.fingerprint,
        "status": finding.status,
        "summary": finding.summary,
        "locations": [location.model_dump() for location in finding.locations],
        "issue": finding.issue,
        "first_seen": first,
        "last_seen": last,
    }


GroupKey = tuple[str, str, str, str | None, str]  # producer, rule, dimension, check, severity


def _groups(runs: Sequence[Run], seen: Seen) -> list[dict[str, Any]]:
    members: dict[GroupKey, list[tuple[Finding, Run]]] = {}
    for run in runs:
        for finding in sorted_findings([f for f in run.findings if not f.suppressions]):
            key = (
                finding.producer,
                finding.rule,
                finding.dimension,
                finding.check,
                finding.severity,
            )
            members.setdefault(key, []).append((finding, run))
    ordered = sorted(members.items(), key=lambda kv: (SEVERITY_ORDER[kv[0][4]], kv[0][0], kv[0][1]))
    return [
        {
            "producer": producer,
            "rule": rule,
            "dimension": dimension,
            "check": check,
            "severity": severity,
            "count": len(pairs),
            "findings": [_group_finding(f, r, seen) for f, r in pairs],
        }
        for (producer, rule, dimension, check, severity), pairs in ordered
    ]


def _suppressed_groups(eval_name: str, runs: Sequence[Run], review: Review) -> list[dict[str, Any]]:
    """One row per suppression rule that applies to the eval, with what it matched in the current view.

    A wildcard rule that matched nothing here is left out; it is noise on this page.
    """
    findings = [f for run in runs for f in run.findings]
    rows: list[dict[str, Any]] = []
    for rule in review.suppressions:
        if rule.subject not in ("*", eval_name):
            continue
        matched = [f for f in findings if rule.matches(f)]
        if rule.subject == "*" and not matched:
            continue
        rows.append(
            {
                "producer": rule.producer or (matched[0].producer if matched else None),
                "rule": rule.rule,
                "count": len(matched),
                "kind": rule.kind,
                "author": display_author(rule.author),
                "reason": rule.reason,
                "since": rule.since.isoformat(),
            }
        )
    return rows


def _issues(eval_name: str, runs: Sequence[Run], review: Review) -> list[dict[str, Any]]:
    linked = Counter(f.issue for run in runs for f in run.findings if f.issue)
    return [
        {
            "id": issue.id,
            "title": issue.title,
            "author": display_author(issue.author),
            "opened": issue.opened.isoformat(),
            "reason": issue.reason,
            "github": issue.github,
            "current": linked.get(issue.id, 0),
        }
        for issue in review.issues
        if issue.subject == eval_name
    ]


def eval_document(
    eval_name: str,
    runs: Sequence[Run],
    review: Review,
    seen: Seen,
    generated_at: datetime,
) -> dict[str, Any]:
    """Everything one eval's page shows: inputs, runs, grouped findings, suppressed groups, issues."""
    runs = sorted(runs, key=lambda r: (r.producer, r.id))
    subject = runs[0].subject
    return {
        "generated_at": iso(generated_at),
        "schema": SCHEMA,
        "eval": eval_name,
        "slug": slug(eval_name),
        "revision": subject.revision.model_dump(),
        "task_version": subject.task_version.full if subject.task_version else None,
        "inputs": inputs_data(runs),
        "runs": [_run_entry(run) for run in runs],
        "groups": _groups(runs, seen),
        "suppressed": _suppressed_groups(eval_name, runs, review),
        "issues": _issues(eval_name, runs, review),
    }


def write_export(
    out: Path,
    runs_by_eval: Mapping[str, Sequence[Run]],
    review: Review,
    *,
    history: Sequence[Run],
    generated_at: datetime | None = None,
) -> list[Path]:
    """Write `export/index.json` and `export/evals/<slug>.json` under `out`."""
    stamp = generated_at or datetime.now(tz=UTC)
    seen = seen_range(history)
    export_dir = out / EXPORT_DIR
    (export_dir / "evals").mkdir(parents=True, exist_ok=True)
    index_path = export_dir / "index.json"
    index_path.write_text(_dumps(index_document(runs_by_eval, review, seen, stamp)))
    written = [index_path]
    for eval_name in sorted(runs_by_eval):
        path = export_dir / "evals" / f"{slug(eval_name)}.json"
        document = eval_document(eval_name, runs_by_eval[eval_name], review, seen, stamp)
        if not _same_but_for_stamp(path, document):
            path.write_text(_dumps(document))
        written.append(path)
    return written


def _same_but_for_stamp(path: Path, document: dict[str, Any]) -> bool:
    """Whether the file already holds this document apart from `generated_at`.

    An eval file's stamp then means "when this eval's data last changed", and a review of one
    eval does not rewrite every other eval's file.
    """
    if not path.is_file():
        return False
    try:
        existing = json.loads(path.read_text())
    except ValueError:
        return False
    if not isinstance(existing, dict):
        return False
    stamp = "generated_at"
    return {k: v for k, v in existing.items() if k != stamp} == {
        k: v for k, v in document.items() if k != stamp
    }


def _dumps(document: dict[str, Any]) -> str:
    return json.dumps(document, indent=1, ensure_ascii=False) + "\n"
