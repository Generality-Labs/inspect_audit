"""The Epoch AI review: derived from a findings bundle, never written by the agent.

A bundle's ratings are the rubric's rows; the sample labels are the defect rate; the
verdict follows the methodology's procedure. These tests build bundles the way an
audit does and check what the publisher derives, refuses and renders.
"""

import asyncio
import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from inspect_audit import _epoch
from inspect_audit._findings import AuditorFindings, Findings, Rating, Review, encoded, read_bundle
from inspect_audit._publishing import load_publisher
from inspect_audit._verdicts import Evidence
from inspect_audit.publishers import epoch

EVIDENCE = [Evidence(observed="what was seen", source="/inputs/source/task.py:12")]
QUESTION_LABELS = {
    "name": "question-labels",
    "description": "labels",
    "grades": ["NO_ISSUE_FOUND", "DEFECT", "UNRESOLVED"],
    "flags": ["DEFECT"],
    "unresolved": ["UNRESOLVED"],
    "details": {},
    "tools": [],
}
GRADING_LABELS = {**QUESTION_LABELS, "name": "grading-labels"}


def take(label: str, by: str = "investigator") -> dict[str, Any]:
    return {"by": by, "label": label, "explanation": "why", "evidence": [], "model": "m"}


def rating(label: str, **extra: Any) -> Rating:
    return Rating(label=label, explanation="Checked and found so.", evidence=EVIDENCE, **extra)


def findings(
    labels: dict[str, str | None],
    ratings: dict[str, Rating],
    *,
    logs: bool = False,
    grading: dict[str, str | None] | None = None,
    reviews: list[Review] | None = None,
) -> Findings:
    """A bundle as an audit leaves it: one question-labels verdict per sample (None for
    a sample no worker finished), the auditor's ratings, and whether logs were given."""
    checks = {"question-labels": QUESTION_LABELS}
    if grading is not None:
        checks["grading-labels"] = GRADING_LABELS
    samples = {}
    for sample, label in labels.items():
        cells: dict[str, list[dict[str, Any]]] = {"question-labels": [take(label)] if label else []}
        if grading is not None:
            graded = grading.get(sample)
            cells["grading-labels"] = [take(graded)] if graded else []
        samples[sample] = cells
    return Findings(
        subject={"task": "bench/simpleqa", "supplied_logs": ["hawk:run-1"] if logs else []},
        investigators={"checks": checks, "samples": samples},
        auditor=AuditorFindings(
            summary="The audit established what it could.",
            ratings=ratings,
            reviews=reviews or [],
        ),
    )


def ten(defects: int) -> dict[str, str | None]:
    return {f"s{i}": "DEFECT" if i < defects else "NO_ISSUE_FOUND" for i in range(10)}


def phase1(defects: int = 1, **more: Rating) -> dict[str, Rating]:
    return {
        "reviewability": rating("Partial"),
        "scoring_integrity": rating("Pass", fields={"sample_design": "random"}),
        "benchmark_consistency": rating("Pass"),
        **more,
    }


# ------------------------------------------------------------------ the description


def test_the_description_is_one_consistent_source(tmp_path: Path) -> None:
    method, page = _epoch.methodology(), _epoch.template()
    assert method.key == "epoch-rubric@1" and page.methodology == method.key
    assert [c.id for c in method.minimum] == [
        "scoring_integrity",
        "benchmark_consistency",
        "elicitation_adequacy",
        "evaluation_bias",
    ]
    assert len(method.quality) == 7 and all(c.needs_logs for c in method.quality)
    assert [c.id for c in method.thresholded] == ["scoring_integrity"]
    _epoch.stage(tmp_path)
    staged = tmp_path / "epoch"
    assert json.loads((staged / "epoch.json").read_text()) == _epoch.description()
    text = (staged / "methodology.md").read_text()
    for check in method.checks:
        assert f"### {check.id}" in text
    for block in page.blocks:
        assert f"### {block.id}" in text
    assert "construct validity last" in text.lower() or "Assessed last" in text


# ------------------------------------------------------------------ phase 1


def test_phase_one_passes_leave_the_review_incomplete() -> None:
    derived = _epoch.validate(findings(ten(1), phase1()))
    assert derived["verdict"] == "Incomplete"
    assert derived["logs_supplied"] is False
    measure = derived["measures"]["scoring_integrity"]
    assert (measure["numerator"], measure["denominator"], measure["exceeded"]) == (1, 10, False)
    assert any("Elicitation, Bias" in r for r in derived["reasons"])
    assert derived["coverage"]["counts"] == {
        "NO_ISSUE_FOUND": 9,
        "DEFECT": 1,
        "UNRESOLVED": 0,
        "NOT_ASSESSED": 0,
    }


def test_a_log_dependent_row_is_refused_without_logs() -> None:
    bundle = findings(ten(1), phase1(elicitation_adequacy=rating("Pass")))
    with pytest.raises(ValueError, match=r"phase 1.*elicitation_adequacy"):
        _epoch.validate(bundle)
    bundle = findings(ten(1), phase1(construct_validity=rating("Measures stated capabilities")))
    with pytest.raises(ValueError, match=r"construct_validity"):
        _epoch.validate(bundle)


def test_a_flag_on_scoring_makes_the_benchmark_flawed_and_stops_the_rubric() -> None:
    flagged = {
        "reviewability": rating("Partial"),
        "scoring_integrity": rating("Flag", fields={"sample_design": "random"}),
    }
    derived = _epoch.validate(findings(ten(3), flagged))
    assert derived["verdict"] == "Flawed"
    assert derived["measures"]["scoring_integrity"]["value"] == 0.3
    # a row rated after the stop is a record the rubric does not allow
    with pytest.raises(ValueError, match=r"stops at the first Flag.*benchmark_consistency"):
        _epoch.validate(findings(ten(3), flagged | {"benchmark_consistency": rating("Pass")}))


def test_the_scoring_label_must_agree_with_the_measured_rate() -> None:
    with pytest.raises(ValueError, match=r"scoring_integrity is Pass but 3/10.*30%.*override"):
        _epoch.validate(findings(ten(3), phase1()))
    overridden = phase1()
    overridden["scoring_integrity"] = rating(
        "Pass", fields={"sample_design": "random"}, override="Two defects are the same typo."
    )
    derived = _epoch.validate(findings(ten(3), overridden))
    assert any("threshold overridden" in r for r in derived["reasons"])


def test_a_purposive_sample_measures_nothing() -> None:
    purposive = phase1()
    purposive["scoring_integrity"] = rating("Pass", fields={"sample_design": "purposive"})
    derived = _epoch.validate(findings(ten(1), purposive))
    assert derived["verdict"] == "Incomplete"
    assert "purposive sample does not measure prevalence" in " ".join(derived["reasons"])
    measure = derived["measures"]["scoring_integrity"]
    # the counts are still reported; only the comparison with the threshold is withheld
    assert (measure["numerator"], measure["denominator"], measure["exceeded"]) == (1, 10, None)
    bare = phase1()
    bare["scoring_integrity"] = rating("Pass")
    with pytest.raises(ValueError, match="sample_design"):
        _epoch.validate(findings(ten(1), bare))


def test_the_auditors_review_stands_over_the_workers_label() -> None:
    labels = ten(2)
    labels["s9"] = None  # a worker that never finished
    review = Review(
        sample_id="s0",
        check="question-labels",
        label="NO_ISSUE_FOUND",
        explanation="The second answer the worker found is the same answer spelt differently.",
        evidence=EVIDENCE,
    )
    derived = _epoch.validate(findings(labels, phase1(), reviews=[review]))
    counts = derived["coverage"]["counts"]
    assert counts == {"NO_ISSUE_FOUND": 8, "DEFECT": 1, "UNRESOLVED": 0, "NOT_ASSESSED": 1}
    assert derived["measures"]["scoring_integrity"]["denominator"] == 9
    assert derived["measures"]["scoring_integrity"]["beside"]["not_assessed"] == 1


def test_reviewability_inadequate_is_not_enough_information() -> None:
    derived = _epoch.validate(findings(ten(1), {"reviewability": rating("Inadequate")}))
    assert derived["verdict"] == "NEI"
    with pytest.raises(ValueError, match="stops at reviewability Inadequate"):
        _epoch.validate(
            findings(
                ten(1),
                {"reviewability": rating("Inadequate"), "benchmark_consistency": rating("Pass")},
            )
        )


def test_ratings_are_the_rubrics_rows_and_nothing_else() -> None:
    with pytest.raises(ValueError, match="overall"):
        _epoch.validate(findings(ten(1), phase1(overall=rating("B"))))
    with pytest.raises(ValueError, match=r"Unknown Epoch check 'C\.1'"):
        _epoch.validate(findings(ten(1), {"C.1": rating("Major")}))
    with pytest.raises(ValueError, match=r"label must be one of Pass, Flag, Not Reviewed"):
        _epoch.validate(findings(ten(1), phase1(benchmark_consistency=rating("Major"))))
    with pytest.raises(ValueError, match="names a model"):
        _epoch.validate(
            findings(
                ten(1),
                phase1(
                    benchmark_consistency=Rating(
                        label="Pass", explanation="GPT-5 mini graded it.", evidence=EVIDENCE
                    )
                ),
            )
        )
    with pytest.raises(ValueError, match="impact belongs to a Flag"):
        _epoch.validate(findings(ten(1), phase1(benchmark_consistency=rating("Pass", impact="x"))))


# ------------------------------------------------------------------ phase 2


def phase2(**more: Rating) -> dict[str, Rating]:
    return phase1(
        elicitation_adequacy=rating("Pass"),
        evaluation_bias=rating("Pass"),
        resource_adequacy=rating("Sufficient"),
        scaffold_fairness=rating("Shared common scaffold"),
        contamination=rating(
            "Assessed",
            fields={"as_of": "2026-11-01", "tasks_public_pct": 100, "solutions_public_pct": 0},
        ),
        human_completability=rating("Not established"),
        score_range=rating("Estimated", fields={"floor": "0%", "ceiling": "95%"}),
        statistical_adequacy=rating("Known", fields={"runs_per_model": 1}),
        construct_validity=rating("Measures stated capabilities"),
        **more,
    )


def test_with_logs_every_row_answered_is_verified() -> None:
    derived = _epoch.validate(findings(ten(1), phase2(), logs=True))
    assert derived["verdict"] == "Verified"
    assert derived["logs_supplied"] is True


def test_a_flag_on_consistency_needs_an_impact_and_stops_the_rubric() -> None:
    with pytest.raises(ValueError, match="needs `impact`"):
        _epoch.validate(findings(ten(1), phase1(benchmark_consistency=rating("Flag"))))
    flagged = phase1(
        benchmark_consistency=rating(
            "Flag", impact="Scores from the two scorer versions differ by 11 points."
        )
    )
    derived = _epoch.validate(findings(ten(1), flagged, logs=True))
    assert derived["verdict"] == "Flawed"
    with pytest.raises(ValueError, match=r"stops at the first Flag.*elicitation_adequacy"):
        _epoch.validate(
            findings(ten(1), flagged | {"elicitation_adequacy": rating("Pass")}, logs=True)
        )


def test_with_grading_labels_a_defect_under_either_check_counts_once() -> None:
    labels = {f"s{i}": "DEFECT" if i == 0 else "NO_ISSUE_FOUND" for i in range(20)}
    grading = dict.fromkeys(labels, "NO_ISSUE_FOUND")
    grading["s0"] = "DEFECT"  # the same item, also misgraded
    grading["s5"] = "DEFECT"  # another, misgraded only
    grading["s19"] = None  # the grading check never reached it
    derived = _epoch.validate(findings(labels, phase2(), logs=True, grading=grading))
    coverage = derived["coverage"]
    assert coverage["counts"]["DEFECT"] == 2 and coverage["counts"]["NOT_ASSESSED"] == 1
    assert coverage["counts_by_check"]["grading-labels"]["DEFECT"] == 2
    measure = derived["measures"]["scoring_integrity"]
    assert (measure["numerator"], measure["denominator"]) == (2, 19)
    assert measure["beside"]["by_check"] == {"question-labels": 1, "grading-labels": 2}


# ------------------------------------------------------------------ the page


def bundle(tmp_path: Path, bundle_findings: Findings) -> Path:
    """A frozen bundle as `freeze` writes it: findings.json and a manifest that names it."""
    root = tmp_path / "bundle"
    root.mkdir()
    content = encoded(bundle_findings.model_dump(mode="json"))
    (root / "findings.json").write_bytes(content)
    manifest = {
        "files": {
            "findings.json": {"sha256": hashlib.sha256(content).hexdigest(), "bytes": len(content)}
        },
        "artifacts": {},
        "log_references": [],
        "jobs": [],
    }
    (root / "manifest.json").write_bytes(encoded(manifest))
    read_bundle(root)
    return root


PROSE = {
    "profile": {"benchmark": "SimpleQA Verified", "creator": "Google"},
    "summary": "The benchmark asks short factual questions. One sampled question has two defensible answers.[^1]",
    "methodology": "Fifty questions were audited against their sources with no evaluation logs.",
    "representative_errors": [
        "Impossible as written (1 row): the key accepts one of two defensible years."
    ],
    "error_examples": [
        {
            "task": "s0",
            "error_type": "Impossible as written",
            "notes": "The question does not say whether the first EP counts as an album.",
            "affected_logs": "Not assessed; no logs supplied",
        }
    ],
    "limitations": ["The evaluation logs were not supplied, so grading was not reviewed."],
    "recommendations": ["Check that each question determines a unique answer."],
    "footnotes": [
        "Sample s0: the key names the 2013 album; the label's discography makes the 2017 one third."
    ],
}


class Box:
    def __init__(self, prose: dict[str, Any]) -> None:
        self.prose = prose
        self.written: dict[str, str] = {}

    async def read_file(self, path: str) -> str:
        assert path == epoch.PROSE
        return json.dumps(self.prose)

    async def write_file(self, path: str, content: str) -> None:
        self.written[path] = content


def test_the_page_is_rendered_from_the_bundle(tmp_path: Path, monkeypatch) -> None:
    source = bundle(tmp_path, findings(ten(1), phase1()))
    monkeypatch.setattr(epoch, "sandbox", lambda: Box(PROSE))
    output = asyncio.run(epoch.build(tmp_path, source))
    page = (output / "epoch_review.md").read_text()
    assert page.startswith("# SimpleQA Verified Review")
    assert "Verdict: Incomplete" in page and "Benchmark creator: Google" in page
    assert "Phase 1 only" in page
    assert "Scoring prevalence: 1 of 10 inspected tasks (10%); threshold 20%." in page
    assert "| s0 | Impossible as written |" in page
    assert "Audited without finding a defect (9): s1, s2" in page
    assert "**Partial**" in page and "**[proceed to 2]**" in page
    assert "**Not Reviewed**" in page  # elicitation and the quality rows wait for phase 2
    assert "[^1]: Sample s0" in page
    assert "without requesting additional information from Google" in page
    verdict = json.loads((output / "verdict.json").read_text())
    assert verdict["verdict"] == "Incomplete" and verdict["logs_supplied"] is False
    assert json.loads((output / "coverage.json").read_text())["counts"]["DEFECT"] == 1
    assert json.loads((output / "epoch.json").read_text()) == _epoch.description()
    assert "Not assessed; no logs supplied" in page


def test_prose_that_oversteps_the_verdict_or_the_defects_is_refused(
    tmp_path: Path, monkeypatch
) -> None:
    from inspect_ai.tool import ToolError

    source = bundle(tmp_path, findings(ten(1), phase1()))
    for change, message in (
        (
            {"interpretation": "Not part of an Incomplete review."},
            "not part of a Incomplete review",
        ),
        ({"error_examples": []}, r"needs the error_examples block"),
        ({"footnotes": []}, r"refers to footnote\(s\) 1"),
        ({"summary": "No marker here."}, r"footnote\(s\) 1 are written but nothing refers"),
        ({"profile": {"benchmark": "X", "creator": ""}}, "creator"),
    ):
        monkeypatch.setattr(epoch, "sandbox", lambda change=change: Box({**PROSE, **change}))
        from inspect_audit.publishers._render import reported

        with pytest.raises(ToolError, match=message), reported("Preview"):
            asyncio.run(epoch.build(tmp_path, source))


def test_a_plain_cell_speaks_of_the_benchmark_not_the_audit(tmp_path: Path, monkeypatch) -> None:
    source = bundle(tmp_path, findings(ten(1), phase1()))
    rows = [{**PROSE["error_examples"][0], "notes": "The auditor probed it and found a defect."}]
    monkeypatch.setattr(epoch, "sandbox", lambda: Box({**PROSE, "error_examples": rows}))
    with pytest.raises(ValueError, match="says how the audit went"):
        asyncio.run(epoch.build(tmp_path, source))


def test_the_publisher_is_wired_into_the_audit() -> None:
    from inspect_ai.tool import read_skills

    publisher = load_publisher("epoch")
    assert publisher.validate is epoch.validate and publisher.stage is _epoch.stage
    assert [s.name for s in read_skills(publisher.skills)] == ["epoch-publishing"]
    assert [s.name for s in read_skills(publisher.audit_skills)] == ["epoch-review"]
    skill = read_skills(publisher.audit_skills)[0].instructions
    for rule in (
        "Every sample ends resolved",
        "More than one defensible answer",
        "credible secondary source",
    ):
        assert rule in skill


def test_an_invalid_epoch_rating_is_refused_before_findings_are_frozen(
    tmp_path: Path, monkeypatch
) -> None:
    from inspect_ai.tool import ToolError
    from inspect_ai.util import Store, store_as
    from inspect_ai.util._store import init_subtask_store

    from inspect_audit import _findings
    from inspect_audit._register import Register, save

    init_subtask_store(Store())
    draft = findings(ten(1), phase1(overall=rating("B")))
    save(Register(subject=draft.subject, checks=["question-labels"], samples={}))

    class Draft:
        async def read_file(self, path: str) -> str:
            return draft.model_dump_json()

    monkeypatch.setattr(_findings, "sandbox", Draft)
    with pytest.raises(ToolError, match=r"ratings\.overall is not part of an Epoch review"):
        asyncio.run(_findings.submit_findings(tmp_path, validate=epoch.validate)())
    assert store_as(_findings.FindingsState).phase == "auditing"


def test_grading_labels_carry_their_own_defect_types_and_a_headline() -> None:
    from inspect_audit._labels import GRADING_DEFECT_TYPES, validate_labels
    from inspect_audit._verdicts import investigation_items

    check = investigation_items(["grading-labels"])[0]
    assert check.flags == ["DEFECT"] and check.unresolved == ["UNRESOLVED"]
    assert "misgraded" in check.details and "grade" in check.tools
    row = {
        "question_id": "s0",
        "status": "DEFECT",
        "checks": ["re-graded the reference answer"],
        "evidence": ["/audit/logs/run.eval sample=s0 epoch=1"],
        "explanation": "The reference answer itself was graded incorrect.",
        "defect_types": ["false_negative"],
    }
    with pytest.raises(ValueError, match="needs a headline"):
        validate_labels([row], ["s0"], require_classification=True, allowed=GRADING_DEFECT_TYPES)
    row["headline"] = 'The grader rejects "Gul Shah" for the reference "Gul Shah".'
    validate_labels([row], ["s0"], require_classification=True, allowed=GRADING_DEFECT_TYPES)
    with pytest.raises(ValueError, match="unknown defect type"):
        validate_labels(
            [{**row, "defect_types": ["missing_information"]}],
            ["s0"],
            require_classification=True,
            allowed=GRADING_DEFECT_TYPES,
        )
    # the item vocabulary is unchanged for question-labels
    with pytest.raises(ValueError, match="unknown defect type"):
        validate_labels([{**row, "headline": None}], ["s0"], require_classification=True)


# ------------------------------------------------------------------ the render command


def test_the_page_is_rendered_again_from_what_the_audit_saved(tmp_path: Path, capsys) -> None:
    source = bundle(tmp_path, findings(ten(1), phase1()))
    prose = tmp_path / "prose.json"
    prose.write_text(json.dumps(PROSE))
    # a bare bundle needs the prose named; a published folder carries its own
    with pytest.raises(SystemExit):
        epoch.main([str(source), "--out", str(tmp_path / "out")])
    assert epoch.main([str(source), "--prose", str(prose), "--out", str(tmp_path / "out")]) == 0
    page = (tmp_path / "out/epoch_review.md").read_text()
    assert "Verdict: Incomplete" in page
    assert "Incomplete" in capsys.readouterr().out
    published = tmp_path / "published"
    (published / "rendered-epoch").mkdir(parents=True)
    (published / "rendered-epoch/prose.json").write_text(json.dumps(PROSE))
    (published / "findings").mkdir()
    for file in source.iterdir():
        (published / "findings" / file.name).write_bytes(file.read_bytes())
    assert epoch.main([str(published), "--out", str(tmp_path / "again")]) == 0
    verdict = json.loads((tmp_path / "again/verdict.json").read_text())
    assert verdict["verdict"] == "Incomplete"
    assert (
        verdict["findings_sha256"]
        == json.loads((source / "manifest.json").read_text())["files"]["findings.json"]["sha256"]
    )
    # findings.json on its own renders too
    assert (
        epoch.main(
            [str(source / "findings.json"), "--prose", str(prose), "--out", str(tmp_path / "bare")]
        )
        == 0
    )


def test_logs_from_another_build_are_a_notice_on_the_page(tmp_path: Path, monkeypatch) -> None:
    """Epoch's logs come from Epoch's package; a reconstruction regrades them. The page
    says so instead of the audit refusing them."""
    with_logs = findings(ten(1), phase1())
    with_logs.subject["supplied_logs"] = ["hawk:imported-epoch"]
    with_logs.subject["logs_provenance"] = [
        "49 of 49 logs produced without recording an epoch-bench version (its recorded "
        "packages: benchmarks 0.1.0); epoch-bench 0.0.1 is installed, so the recorded grades "
        "come from a different build of the benchmark"
    ]
    source = bundle(tmp_path, with_logs)
    monkeypatch.setattr(epoch, "sandbox", lambda: Box(PROSE))
    page = (asyncio.run(epoch.build(tmp_path, source)) / "epoch_review.md").read_text()
    assert "recorded by another build of the benchmark" in page
    assert "benchmarks 0.1.0" in page
    assert "Phase 1 only" not in page
