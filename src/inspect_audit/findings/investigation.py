"""A published investigation bundle as one run in the store.

The investigator publishes `published/<id>/` with `findings.json`, `assessments.json`,
`coverage.json` and the report. This reads the GL-format bundle as plain JSON (the
findings package imports nothing from the investigator) and returns one run of the
investigator, plus the leads its findings cite, which the caller writes as status
decisions. The bundle itself stays where it is; the run cites it by id and path.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import Any, cast

from pydantic import JsonValue

from .adapters import slug
from .fingerprint import fingerprint
from .models import (
    AnyLocation,
    ArtifactLocation,
    Assessment,
    Finding,
    Measure,
    Revision,
    Run,
    Severity,
    Source,
    Status,
    Subject,
)

PRODUCER = "inspect_audit_investigate"
# the GL report framework's checks are this taxonomy's letter codes and contributions
METHODOLOGY = "inspect-audit@1"
SOURCE_FORMAT = "inspect_audit.investigation.finding"
LIMITATION = "audit_limitation"
# the reason on a lead's status decision; leads leave these statuses out, so an earlier
# investigation's verdict does not anchor the next one
CITED = "cited as evidence by "
# a lead takes only a confirming status; a retracted or unconfirmed investigator finding
# says nothing about whether the deterministic observation holds
MARKS: tuple[Status, ...] = ("supported", "qualified")


@dataclass(frozen=True)
class Citation:
    """A lead an investigator finding rests on: the lead's record id, and what the citing finding concluded."""

    record_id: str
    status: Status
    cited_by: str


@dataclass(frozen=True)
class Imported:
    run: Run
    citations: list[Citation]


# The dataset adapter's rule: a quote keeps enough to recognise a sample, too little to
# reproduce it, since some datasets are gated to keep their text out of training data
QUOTE_CHARS = 32


def _short(quote: object) -> str | None:
    if quote is None:
        return None
    text = str(quote)
    return text if len(text) <= QUOTE_CHARS else f"{text[:QUOTE_CHARS]}…"


def _shortened(raw: dict[str, Any]) -> dict[str, Any]:
    """The bundle's finding with each evidence quote shortened, as it is stored."""
    refs = [ref | {"quote": _short(ref.get("quote"))} for ref in raw.get("evidence", [])]
    return raw | {"evidence": refs}


def _is_lead(path: str) -> bool:
    return path.endswith("findings/LEADS.md") or "findings/runs/" in path


def _commitref(bundle: Path) -> str | None:
    metadata = bundle / "metadata.tex"
    if not metadata.is_file():
        return None
    match = re.search(r"\\commitref\{([^}]*)\}", metadata.read_text())
    return match.group(1).strip() or None if match else None


def _load(path: Path) -> Any:
    return json.loads(path.read_text()) if path.is_file() else None


def read_bundle(
    bundle: Path, eval: str, *, revision: str | None = None, timestamp: datetime | None = None
) -> Imported:
    """The run a published bundle describes.

    `revision` is used when the report names no commit. `timestamp` replaces
    `findings.json`'s modification time, which a copy or download resets.
    """
    bundle = bundle.resolve()
    findings_file = bundle / "findings.json"
    if not findings_file.is_file():
        raise ValueError(f"{bundle} is not a published investigation: it has no findings.json")
    raw_findings: list[dict[str, Any]] = json.loads(findings_file.read_text())
    commit = _commitref(bundle) or revision
    if commit is None:
        raise ValueError(f"{bundle}'s report names no commit; pass the audited revision")
    stamp = (timestamp or datetime.fromtimestamp(findings_file.stat().st_mtime, UTC)).replace(
        microsecond=0
    )
    run_id = f"{PRODUCER}-{stamp:%Y%m%dT%H%M%SZ}-{slug(eval)}"
    subject = Subject(eval=eval, revision=Revision(commit=commit))

    findings: list[Finding] = []
    limitations: list[dict[str, JsonValue]] = []
    pending: list[tuple[str, Status, int]] = []  # lead record id, citing status, finding index
    fingerprints: dict[str, str] = {}
    for raw in map(_shortened, raw_findings):
        if raw["origin"] == LIMITATION:
            limitations.append({"id": raw["id"], "claim": raw["claim"]})
            continue
        refs: list[dict[str, Any]] = raw["evidence"]
        leads = [ref for ref in refs if _is_lead(ref["path"])]
        located: list[dict[str, Any]] = [ref for ref in refs if not _is_lead(ref["path"])] or leads
        if not located:  # the schema allows a finding with no evidence; locate it in the bundle
            located = [{"path": "findings.json", "location": raw["id"], "quote": None}]
        locations: list[AnyLocation] = [
            ArtifactLocation(
                role="primary" if index == 0 else "related",
                path=ref["path"],
                location=ref.get("location"),
                quote=ref.get("quote"),
            )
            for index, ref in enumerate(located)
        ]
        finding = Finding(
            fingerprint=fingerprint(PRODUCER, raw["id"], eval, locations[0]),
            producer=PRODUCER,
            rule=raw["id"],
            subject=subject,
            dimension=raw["section"],
            severity=_severity(raw),
            status=raw["status"],
            summary=raw["claim"],
            locations=locations,
            run_id=run_id,
            source=Source(format=SOURCE_FORMAT, record=cast(JsonValue, raw)),
        )
        fingerprints[raw["id"]] = finding.fingerprint
        status: Status = raw["status"]
        if status not in MARKS:
            leads = []
        pending.extend(
            (str(ref["location"]), status, len(findings)) for ref in leads if ref.get("location")
        )
        findings.append(finding)

    limitation_ids = {str(item["id"]) for item in limitations}
    assessments = [
        _assessment(row, fingerprints, limitation_ids)
        for row in _load(bundle / "assessments.json") or []
    ]
    inputs: dict[str, JsonValue] = {
        "bundle": {
            "id": bundle.name,
            "path": str(bundle),
            "findings_sha256": sha256(findings_file.read_bytes()).hexdigest(),
        }
    }
    coverage = _load(bundle / "coverage.json")
    if isinstance(coverage, dict) and "counts" in coverage:
        inputs["coverage"] = {
            "denominator": coverage.get("denominator"),
            "counts": coverage["counts"],
        }
    if limitations:
        inputs["limitations"] = cast(JsonValue, limitations)
    run = Run(
        id=run_id,
        timestamp=stamp,
        producer=PRODUCER,
        subject=subject,
        inputs=inputs,
        findings=findings,
        assessments=assessments,
    )
    citations = [
        Citation(record_id=record, status=status, cited_by=str(run.findings[index].id))
        for record, status, index in pending
    ]
    return Imported(run=run, citations=citations)


SEVERITIES: tuple[Severity, ...] = ("none", "minor", "major", "critical")


def _severity(raw: dict[str, Any]) -> Severity:
    # the bundle writes Minor/Major/Critical, and null (older bundles: "None") for no defect
    value = str(raw.get("severity") or "none").lower()
    for severity in SEVERITIES:
        if value == severity:
            return severity
    raise ValueError(f"finding {raw['id']!r} has an unknown severity {raw.get('severity')!r}")


def _assessment(
    row: dict[str, Any], fingerprints: dict[str, str], limitation_ids: set[str]
) -> Assessment:
    cited = [fingerprints[e] for e in row.get("evidence", []) if e in fingerprints]
    files = [
        ArtifactLocation(path=e)
        for e in row.get("evidence", [])
        if e not in fingerprints and e not in limitation_ids
    ]
    checked, affected = row.get("checked_ids"), row.get("affected_ids")
    measure = (
        Measure(name="affected_items", numerator=len(affected), denominator=len(checked))
        if checked and affected is not None
        else None
    )
    return Assessment(
        methodology=METHODOLOGY,
        check=row["id"],
        level=row["assessment"],
        notes=row.get("result"),
        evidence=list(files),
        findings=cited,
        measure=measure,
    )
