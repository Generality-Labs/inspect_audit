from __future__ import annotations

import json
import os
from datetime import UTC, datetime
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
        {
            "path": "_inputs/source/src/inspect_evals/bfcl/score/scorer.py",
            "location": "lines 20-27",
            "quote": None,
        },
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
COVERAGE = {
    "denominator": 4981,
    "counts": {"NO_ISSUE_FOUND": 0, "DEFECT": 0, "UNRESOLVED": 0, "NOT_ASSESSED": 4981},
}


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
    assert (construct.methodology, construct.check, construct.level) == (
        "inspect-audit@1",
        "C",
        "Minor",
    )
    assert construct.notes == ASSESSMENTS[0]["result"]
    assert construct.findings == [contradictory.fingerprint]
    assert construct.evidence == [ArtifactLocation(path="evidence/pairs_probe.txt")]
    assert construct.measure is not None
    assert (construct.measure.name, construct.measure.numerator, construct.measure.denominator) == (
        "affected_items",
        1,
        4,
    )
    assert (informativeness.check, informativeness.level, informativeness.measure) == (
        "I",
        "Not assessed",
        None,
    )


def test_a_bundle_without_assessments_or_coverage_still_imports(tmp_path: Path) -> None:
    run = _read(_bundle(tmp_path, assessments=None, coverage=None)).run
    assert run.assessments == [] and "coverage" not in run.inputs


def test_the_revision_comes_from_the_command_line_when_the_report_names_none(
    tmp_path: Path,
) -> None:
    bundle = _bundle(tmp_path, commitref=None)
    assert _read(bundle, revision="7158d455").run.subject.revision.commit == "7158d455"
    with pytest.raises(ValueError, match="revision"):
        _read(bundle)


def test_a_directory_without_findings_is_not_a_bundle(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match=r"findings\.json"):
        _read(tmp_path)


STEREOSET = "inspect_evals/stereoset"
AUTHOR = ["--author", "Matt Fisher <matt@example.com>"]


def _stereoset_store(tmp_path: Path, run: Any) -> Path:
    from inspect_audit.findings.cli import write_outputs

    out = tmp_path / "store"
    write_outputs(out, {STEREOSET: [run]})
    return out


def _citing(lead: str) -> dict[str, Any]:
    refs = [{"path": "_inputs/findings/LEADS.md", "location": lead, "quote": None}]
    return CONTRADICTORY | {"evidence": refs + CONTRADICTORY["evidence"][1:]}


def _import(bundle: Path, store: Path, *extra: str) -> int:
    from inspect_audit.findings.cli import main

    return main(
        ["import-investigation", str(bundle), "--store", str(store), "--eval", STEREOSET, *extra]
    )


def test_import_writes_the_run_and_the_export_carries_its_assessments(
    tmp_path: Path, run: Any
) -> None:
    store = _stereoset_store(tmp_path, run)
    assert _import(_bundle(tmp_path, findings=[_citing("lint-1/1"), SCORER]), store, *AUTHOR) == 0
    doc = json.loads((store / "export" / "evals" / "inspect-evals-stereoset.json").read_text())
    (entry,) = [r for r in doc["runs"] if r["producer"] == "inspect_audit_investigate"]
    assert [a["check"] for a in entry["assessments"]] == ["C", "I"]
    groups = {g["rule"] for g in doc["groups"]}
    assert {"contradictory-relevance", "agentic-containment"} <= groups


def test_a_cited_lead_takes_the_citing_status_with_its_provenance(tmp_path: Path, run: Any) -> None:
    from inspect_audit.findings.fs import StoreFS
    from inspect_audit.findings.review import load_review

    store = _stereoset_store(tmp_path, run)
    assert _import(_bundle(tmp_path, findings=[_citing("lint-1/1")]), store, *AUTHOR) == 0
    review = load_review(StoreFS.from_locator(store))
    assert review.statuses == {"sha256:0": "supported"}
    reason = review.status_provenance["sha256:0"].reason or ""
    assert reason.startswith("cited as evidence by inspect_audit_investigate-")


def test_a_bundle_imported_again_adds_no_second_run(
    tmp_path: Path, run: Any, capsys: pytest.CaptureFixture[str]
) -> None:
    store = _stereoset_store(tmp_path, run)
    bundle = _bundle(tmp_path, findings=[SCORER])
    assert _import(bundle, store) == 0
    assert _import(bundle, store) == 0
    assert "already in the store as inspect_audit_investigate-" in capsys.readouterr().out
    assert (
        len(list((store / "inspect-evals-stereoset" / "runs").glob("inspect_audit_investigate-*")))
        == 1
    )


def test_a_changed_bundle_under_the_same_id_is_refused(
    tmp_path: Path, run: Any, capsys: pytest.CaptureFixture[str]
) -> None:
    store = _stereoset_store(tmp_path, run)
    bundle = _bundle(tmp_path, findings=[SCORER])
    assert _import(bundle, store) == 0
    (bundle / "findings.json").write_text(json.dumps([SCORER | {"claim": "corrected"}]))
    assert _import(bundle, store) == 2
    assert "changed since" in capsys.readouterr().err


def test_a_citation_outside_the_current_view_warns_and_still_imports(
    tmp_path: Path, run: Any, capsys: pytest.CaptureFixture[str]
) -> None:
    from inspect_audit.findings.fs import StoreFS
    from inspect_audit.findings.review import load_review

    store = _stereoset_store(tmp_path, run)
    assert _import(_bundle(tmp_path, findings=[_citing(LEAD)]), store) == 0
    assert LEAD in capsys.readouterr().err
    assert load_review(StoreFS.from_locator(store)).statuses == {}
    assert list((store / "inspect-evals-stereoset" / "runs").glob("inspect_audit_investigate-*"))


def test_citations_need_an_author(
    tmp_path: Path, run: Any, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)  # no git identity to fall back on
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(tmp_path / "none"))
    store = _stereoset_store(tmp_path, run)
    assert _import(_bundle(tmp_path, findings=[_citing("lint-1/1")]), store) == 2
    assert "author" in capsys.readouterr().err
    assert not list(
        (store / "inspect-evals-stereoset" / "runs").glob("inspect_audit_investigate-*")
    )


def test_leads_leave_out_investigations(tmp_path: Path, run: Any) -> None:
    from inspect_audit.findings.cli import main

    store = _stereoset_store(tmp_path, run)
    assert _import(_bundle(tmp_path, findings=[SCORER]), store) == 0
    bundle = tmp_path / "leads"
    assert main(["leads", "--store", str(store), "--bundle", str(bundle), STEREOSET]) == 0
    assert "agentic-containment" not in (bundle / "LEADS.md").read_text()
    assert [p.name for p in (bundle / "runs").iterdir()] == ["lint-1.run.json"]


def test_quoted_sample_text_is_shortened_as_dataset_findings_are(tmp_path: Path) -> None:
    long = "calculate the area of a triangle with base 10 and height 5 please"
    quoting = SCORER | {
        "evidence": [{"path": "evidence/x.txt", "location": "row 1", "quote": long}]
    }
    (finding,) = _read(_bundle(tmp_path, findings=[quoting], assessments=None)).run.findings
    shortened = long[:32] + "…"
    assert finding.primary_location.quote == shortened
    assert finding.source.record["evidence"][0]["quote"] == shortened  # type: ignore[index,call-overload]


def _decisions(store: Path) -> list[Any]:
    from inspect_audit.findings.fs import StoreFS
    from inspect_audit.findings.review import load_decisions

    return [d for d in load_decisions(StoreFS.from_locator(store)) if d.status is not None]


def test_a_malformed_author_fails_before_anything_is_written(tmp_path: Path, run: Any) -> None:
    store = _stereoset_store(tmp_path, run)
    bundle = _bundle(tmp_path, findings=[_citing("lint-1/1")])
    assert _import(bundle, store, "--author", "Matt") == 2
    assert not list(
        (store / "inspect-evals-stereoset" / "runs").glob("inspect_audit_investigate-*")
    )
    assert _import(bundle, store, *AUTHOR) == 0
    assert len(_decisions(store)) == 1


def test_an_interrupted_import_resumes_with_the_missing_lead_decisions(
    tmp_path: Path, run: Any
) -> None:
    store = _stereoset_store(tmp_path, run)
    bundle = _bundle(tmp_path, findings=[_citing("lint-1/1")])
    assert _import(bundle, store, *AUTHOR) == 0
    for decision in (store / "review").glob("*.json"):
        decision.unlink()  # as if the run was written and the status write then failed
    assert _import(bundle, store, *AUTHOR) == 0
    assert len(_decisions(store)) == 1
    assert _import(bundle, store, *AUTHOR) == 0
    assert len(_decisions(store)) == 1


@pytest.mark.parametrize("status", ["hypothesis", "retracted"])
def test_only_supported_or_qualified_findings_mark_a_lead(
    tmp_path: Path, run: Any, status: str
) -> None:
    store = _stereoset_store(tmp_path, run)
    assert (
        _import(
            _bundle(tmp_path, findings=[_citing("lint-1/1") | {"status": status}]), store, *AUTHOR
        )
        == 0
    )
    assert _decisions(store) == []


def test_a_lead_cited_twice_takes_the_stronger_status_once(tmp_path: Path, run: Any) -> None:
    store = _stereoset_store(tmp_path, run)
    supported = _citing("lint-1/1")
    qualified = _citing("lint-1/1") | {"id": "second", "status": "qualified"}
    assert _import(_bundle(tmp_path, findings=[supported, qualified]), store, *AUTHOR) == 0
    (decision,) = _decisions(store)
    assert decision.status.status == "supported"


def test_a_lead_someone_already_judged_is_left_alone(tmp_path: Path, run: Any) -> None:
    from inspect_audit.findings.cli import main

    store = _stereoset_store(tmp_path, run)
    human = [
        "review",
        "status",
        "--store",
        str(store),
        "--id",
        "lint-1/1",
        "qualified",
        "--reason",
        "partly",
    ]
    assert main([*human, *AUTHOR]) == 0
    assert _import(_bundle(tmp_path, findings=[_citing("lint-1/1")]), store, *AUTHOR) == 0
    (decision,) = _decisions(store)
    assert decision.status.status == "qualified"


def test_the_timestamp_can_be_given_when_the_file_time_is_a_copy_time(tmp_path: Path) -> None:
    run = _read(_bundle(tmp_path), timestamp=datetime(2026, 9, 25, 10, tzinfo=UTC)).run
    assert run.id == "inspect_audit_investigate-20260925T100000Z-inspect-evals-bfcl"


def test_bundled_leads_carry_no_status_from_investigator_citations(
    tmp_path: Path, run: Any
) -> None:
    from inspect_audit.findings.cli import main

    store = _stereoset_store(tmp_path, run)
    assert _import(_bundle(tmp_path, findings=[_citing("lint-1/1")]), store, *AUTHOR) == 0
    leads = tmp_path / "leads"
    assert main(["leads", "--store", str(store), "--bundle", str(leads), STEREOSET]) == 0
    stored = json.loads((leads / "runs" / "lint-1.run.json").read_text())
    assert stored["findings"][0]["history"] == []
    assert "cited as evidence" not in (leads / "LEADS.md").read_text()


def test_a_finding_without_evidence_is_located_in_the_bundle(tmp_path: Path) -> None:
    bare = SCORER | {"evidence": []}
    (finding,) = _read(_bundle(tmp_path, findings=[bare], assessments=None)).run.findings
    assert finding.primary_location == ArtifactLocation(
        role="primary", path="findings.json", location="agentic-containment"
    )


def test_a_bundle_named_as_the_current_directory_keeps_its_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bundle = _bundle(tmp_path)
    monkeypatch.chdir(bundle)
    assert _read(Path(".")).run.inputs["bundle"]["id"] == bundle.name  # type: ignore[index]


def test_citations_of_another_evals_leads_are_refused(
    tmp_path: Path, run: Any, capsys: pytest.CaptureFixture[str]
) -> None:
    from inspect_audit.findings.cli import main

    store = _stereoset_store(tmp_path, run)
    bundle = _bundle(tmp_path, findings=[_citing("lint-1/1")])
    args = [
        "import-investigation",
        str(bundle),
        "--store",
        str(store),
        "--eval",
        "inspect_evals/other",
    ]
    assert main([*args, *AUTHOR]) == 2
    assert STEREOSET in capsys.readouterr().err
