from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest

from inspect_audit.findings.models import ArtifactLocation

EVAL = "inspect_evals/bfcl"
LEAD = "inspect_dataset-20261008T050851Z-inspect-evals-bfcl/3"

CONTRADICTORY: dict[str, Any] = {
    "id": "contradictory-relevance",
    "section": "dataset",
    "claim": "Identical question-and-tool configurations are labelled both irrelevant and relevant.",
    "status": "supported",
    "origin": "source",
    "severity": "Minor",
    "evidence": [
        {"path": "_inputs/findings/LEADS.md", "location": LEAD, "quote": None},
        {"path": "evidence/pairs_probe.txt", "location": "three pairs", "quote": "with call: 0 1"},
        {"path": "_inputs/source/src/inspect_evals/bfcl/score/scorer.py", "location": "lines 20-27", "quote": None},
    ],
    "reproduce": "python evidence/pairs_probe.py",
}
SCORER: dict[str, Any] = {
    "id": "agentic-containment",
    "section": "grading",
    "claim": "The final-answer scorer passes any response that contains the gold string.",
    "status": "supported",
    "origin": "experiment",
    "severity": "Major",
    "evidence": [{"path": "evidence/agentic_probe.txt", "location": "rows 1-4", "quote": None}],
    "reproduce": "python evidence/agentic_probe.py",
}
NO_LOGS: dict[str, Any] = {
    "id": "no-logs",
    "section": "informativeness",
    "claim": "No Inspect logs were supplied, so score effects were not measured.",
    "status": "supported",
    "origin": "audit_limitation",
    "severity": None,
    "evidence": [{"path": "coverage.json", "location": "counts", "quote": None}],
    "reproduce": "inspect coverage.json",
}
ASSESSMENTS: list[dict[str, Any]] = [
    {
        "id": "C",
        "assessment": "Minor",
        "result": "Relevance labels contradict each other in three matched pairs.",
        "evidence": ["contradictory-relevance", "evidence/pairs_probe.txt", "no-logs"],
        "checked_ids": ["a", "b", "c", "d"],
        "affected_ids": ["a"],
    },
    {"id": "I", "assessment": "Not assessed", "result": "No logs.", "evidence": []},
]
COVERAGE = {"denominator": 4981, "counts": {"NO_ISSUE_FOUND": 0, "DEFECT": 0, "UNRESOLVED": 0, "NOT_ASSESSED": 4981}}


def _bundle(
    tmp_path: Path,
    findings: list[dict[str, Any]] | None = None,
    assessments: list[dict[str, Any]] | None = ASSESSMENTS,
    coverage: dict[str, Any] | None = COVERAGE,
    commitref: str | None = "dbd3dd25",
) -> Path:
    bundle = tmp_path / "published" / "a5198d4b85aa45748841c5cea06ca318"
    bundle.mkdir(parents=True)
    (bundle / "findings.json").write_text(json.dumps(findings or [CONTRADICTORY, SCORER, NO_LOGS]))
    if assessments is not None:
        (bundle / "assessments.json").write_text(json.dumps(assessments))
    if coverage is not None:
        (bundle / "coverage.json").write_text(json.dumps(coverage))
    ref = f"\\commitref{{{commitref}}}" if commitref else "\\commitref{}"
    (bundle / "metadata.tex").write_text(f"\\reporttype{{AUDIT}}\n\\evalname{{BFCL}}\n{ref}\n")
    stamp = 1_791_000_000  # 2026-10-03T04:00:00Z
    os.utime(bundle / "findings.json", (stamp, stamp))
    return bundle


def _read(bundle: Path, **kwargs: Any):  # type: ignore[no-untyped-def]
    from inspect_audit.findings.investigation import read_bundle

    return read_bundle(bundle, EVAL, **kwargs)


def test_a_bundle_becomes_one_run_of_the_investigator(tmp_path: Path) -> None:
    run = _read(_bundle(tmp_path)).run
    assert run.producer == "inspect_audit_investigate"
    assert run.id == "inspect_audit_investigate-20261003T040000Z-inspect-evals-bfcl"
    assert run.subject.eval == EVAL and run.subject.revision.commit == "dbd3dd25"
    assert run.inputs["bundle"]["id"] == "a5198d4b85aa45748841c5cea06ca318"  # type: ignore[index]
    assert run.inputs["coverage"] == COVERAGE


def test_a_finding_keeps_its_section_severity_status_and_claim(tmp_path: Path) -> None:
    run = _read(_bundle(tmp_path)).run
    scorer = next(f for f in run.findings if f.rule == "agentic-containment")
    assert (scorer.dimension, scorer.severity, scorer.status) == ("grading", "major", "supported")
    assert scorer.summary == SCORER["claim"]
    assert scorer.primary_location == ArtifactLocation(
        role="primary", path="evidence/agentic_probe.txt", location="rows 1-4"
    )
    assert scorer.source.record == SCORER
    assert scorer.fingerprint.startswith("sha256:")


def test_lead_references_become_citations_not_locations(tmp_path: Path) -> None:
    imported = _read(_bundle(tmp_path))
    contradictory = next(f for f in imported.run.findings if f.rule == "contradictory-relevance")
    assert [loc.path for loc in contradictory.locations] == [  # type: ignore[union-attr]
        "evidence/pairs_probe.txt",
        "_inputs/source/src/inspect_evals/bfcl/score/scorer.py",
    ]
    (citation,) = imported.citations
    assert (citation.record_id, citation.status, citation.cited_by) == (
        LEAD,
        "supported",
        contradictory.id,
    )


def test_a_finding_resting_only_on_a_lead_keeps_the_lead_as_its_location(tmp_path: Path) -> None:
    lead_only = CONTRADICTORY | {"evidence": [CONTRADICTORY["evidence"][0]]}
    imported = _read(_bundle(tmp_path, findings=[lead_only], assessments=None))
    (finding,) = imported.run.findings
    assert finding.primary_location == ArtifactLocation(
        role="primary", path="_inputs/findings/LEADS.md", location=LEAD
    )
    assert len(imported.citations) == 1


def test_audit_limitations_stay_out_of_the_findings(tmp_path: Path) -> None:
    run = _read(_bundle(tmp_path)).run
    assert "no-logs" not in {f.rule for f in run.findings}
    assert run.inputs["limitations"] == [{"id": "no-logs", "claim": NO_LOGS["claim"]}]


def test_assessments_cite_findings_by_fingerprint_and_files_as_evidence(tmp_path: Path) -> None:
    run = _read(_bundle(tmp_path)).run
    contradictory = next(f for f in run.findings if f.rule == "contradictory-relevance")
    construct, informativeness = run.assessments
    assert (construct.methodology, construct.check, construct.level) == ("inspect-audit@1", "C", "Minor")
    assert construct.notes == ASSESSMENTS[0]["result"]
    assert construct.findings == [contradictory.fingerprint]
    assert construct.evidence == [ArtifactLocation(path="evidence/pairs_probe.txt")]
    assert construct.measure is not None
    assert (construct.measure.name, construct.measure.numerator, construct.measure.denominator) == (
        "affected_items",
        1,
        4,
    )
    assert (informativeness.check, informativeness.level, informativeness.measure) == ("I", "Not assessed", None)


def test_a_bundle_without_assessments_or_coverage_still_imports(tmp_path: Path) -> None:
    run = _read(_bundle(tmp_path, assessments=None, coverage=None)).run
    assert run.assessments == [] and "coverage" not in run.inputs


def test_the_revision_comes_from_the_command_line_when_the_report_names_none(tmp_path: Path) -> None:
    bundle = _bundle(tmp_path, commitref=None)
    assert _read(bundle, revision="7158d455").run.subject.revision.commit == "7158d455"
    with pytest.raises(ValueError, match="revision"):
        _read(bundle)


def test_a_directory_without_findings_is_not_a_bundle(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="findings.json"):
        _read(tmp_path)
