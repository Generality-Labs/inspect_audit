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

from inspect_audit import _epoch, _findings
from inspect_audit._findings import AuditorFindings, Findings, Rating, Review, encoded, read_bundle
from inspect_audit._publishing import load_publisher
from inspect_audit._register import Register
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
    # the page ends after the errors and the notices: the summary's footnote and the
    # disclaimer are not printed, and the prose the page leaves out is kept
    assert "[^1]" not in page and "## Footnotes" not in page
    assert "without requesting additional information from Google" not in page
    assert json.loads((output / "prose.json").read_text())["summary"] == PROSE["summary"]
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


def test_the_errors_alone_make_a_page(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The blocks the page does not render are optional; only cited footnotes print."""
    page = _epoch.template()
    assert page.ends_after_opening
    summary = page.block("summary")
    assert summary.required and not page.renders(summary)
    rendered = {"representative_errors", "error_examples"}
    assert {b.id for b in page.blocks if page.renders(b)} == rendered
    source = bundle(tmp_path, findings(ten(1), phase1()))
    minimal = {
        "profile": PROSE["profile"],
        "representative_errors": ["Impossible as written (1 row): two defensible years.[^1]"],
        "error_examples": PROSE["error_examples"],
        "footnotes": PROSE["footnotes"],
    }
    monkeypatch.setattr(epoch, "sandbox", lambda: Box(minimal))
    output = asyncio.run(epoch.build(tmp_path, source))
    text = (output / "epoch_review.md").read_text()
    assert "## Footnotes\n\n[^1]: Sample s0" in text
    assert text.index("Phase 1 only") < text.index("## Footnotes")
    assert "summary" not in json.loads((output / "prose.json").read_text())
    staged = _epoch.methodology_markdown(_epoch.methodology(), page)
    assert "### summary (not rendered; optional)" in staged and "leave them out" in staged


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
        "epoch_class": "false_negative",
        "sub_reason": "overly_strict_scorer",
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
    # a recorded grade is wrong one way or the other: never impossible as written
    with pytest.raises(ValueError, match="epoch_class must be one of false_negative"):
        validate_labels(
            [{**row, "epoch_class": "impossible_as_written", "sub_reason": "ambiguous_wording"}],
            ["s0"],
            require_classification=True,
            allowed=GRADING_DEFECT_TYPES,
        )


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


# ------------------------------------------------------------------ order and sources


def sourced(answer: str, url: str, *, key: bool = False) -> dict[str, Any]:
    return {
        "answer": answer,
        "source": f"Catalogue of {answer}",
        "url": url,
        "quote": f"The first EP was {answer}.",
        "key": key,
    }


def twenty(defects: int) -> dict[str, str | None]:
    return {f"s{i}": "DEFECT" if i < defects else "NO_ISSUE_FOUND" for i in range(20)}


def with_sources(bundle_findings: Findings, sample: str, sources: list[dict[str, Any]]) -> Findings:
    """An investigator's question-labels verdict whose assessment row lists sources."""
    samples = bundle_findings.investigators["samples"]
    assert isinstance(samples, dict)
    cells = samples[sample]
    assert isinstance(cells, dict)
    cells["question-labels"] = [
        {
            **take("DEFECT"),
            "record": {
                "question_assessments": [
                    {"question_id": sample, "status": "DEFECT", "sources": sources}
                ]
            },
        }
    ]
    return bundle_findings


def test_the_page_opens_with_the_rubric_then_the_representative_errors(
    tmp_path: Path, monkeypatch
) -> None:
    source = bundle(tmp_path, findings(ten(1), phase1()))
    monkeypatch.setattr(epoch, "sandbox", lambda: Box(PROSE))
    page = (asyncio.run(epoch.build(tmp_path, source)) / "epoch_review.md").read_text()
    order = [
        "Verdict: Incomplete",
        "## Benchmark Review Rubric",
        "### 3. Evaluation Quality",
        "## Representative Errors",
        "| s0 | Impossible as written |",
        "Audited without finding a defect (9)",
        "Phase 1 only",
    ]
    positions = [page.index(marker) for marker in order]
    assert positions == sorted(positions)
    assert page.rstrip().endswith("*")  # the notice is the last thing on the page
    for dropped in ("Summary", "Methodology", "Limitations", "Disclaimer", "Recommendations"):
        assert f"## {dropped}" not in page
    # findings without sources print no sources section
    assert "Competing answers" not in page


def test_competing_answers_are_printed_with_their_sources(tmp_path: Path, monkeypatch) -> None:
    recorded = with_sources(
        findings(ten(1), phase1()),
        "s0",
        [
            sourced("Long Good Bye", "https://label.example/long-good-bye", key=True),
            sourced("The First Mini Demo Album", "https://catalogue.example/7082924"),
        ],
    )
    source = bundle(tmp_path, recorded)
    monkeypatch.setattr(epoch, "sandbox", lambda: Box(PROSE))
    output = asyncio.run(epoch.build(tmp_path, source))
    page = (output / "epoch_review.md").read_text()
    section = page[page.index("### Competing answers and their sources") :]
    assert "**s0**: Impossible as written" in section and "## Summary" not in page
    assert (
        "- **Long Good Bye** (the reference answer): Catalogue of Long Good Bye, “The first EP "
        "was Long Good Bye.” <https://label.example/long-good-bye>"
    ) in section
    assert "- **The First Mini Demo Album**: Catalogue of" in section
    assert json.loads((output / "sources.json").read_text())["s0"][1]["url"].endswith("7082924")

    # the auditor's review, where it gives sources, replaces the worker's
    reviewed = recorded.model_copy(deep=True)
    reviewed.auditor.reviews = [
        Review(
            sample_id="s0",
            check="question-labels",
            label="DEFECT",
            explanation="Two catalogues disagree.",
            evidence=EVIDENCE,
            sources=[
                sourced("1925", "FRUS 1969-76, vol. XXV, doc. 35", key=True),
                sourced("1927", "https://history.example/stations"),
            ],
        )
    ]
    (tmp_path / "r").mkdir()
    page = (
        asyncio.run(epoch.build(tmp_path, bundle(tmp_path / "r", reviewed))) / "epoch_review.md"
    ).read_text()
    assert (
        "- **1925** (the reference answer): Catalogue of 1925, “The first EP was 1925.” FRUS"
        in page
    )
    assert "Long Good Bye" not in page


def test_a_defect_without_sources_is_named_beside_those_with_them(
    tmp_path: Path, monkeypatch
) -> None:
    recorded = with_sources(
        findings(twenty(2), phase1()),
        "s0",
        [sourced("A", "https://a.example"), sourced("B", "https://b.example", key=True)],
    )
    rows = [
        PROSE["error_examples"][0],
        {**PROSE["error_examples"][0], "task": "s1", "notes": "The grader rejects a valid unit."},
    ]
    prose = {**PROSE, "error_examples": rows}
    monkeypatch.setattr(epoch, "sandbox", lambda: Box(prose))
    page = (
        asyncio.run(epoch.build(tmp_path, bundle(tmp_path, recorded))) / "epoch_review.md"
    ).read_text()
    assert "**s0**: Impossible as written" in page and "**s1**" not in page
    assert "No competing sources recorded (1): s1" in page


def test_errors_are_grouped_by_epoch_class_with_each_sub_reason(
    tmp_path: Path, monkeypatch
) -> None:
    recorded = with_sources(
        findings(twenty(3), phase1()),
        "s0",
        [sourced("A", "https://a.example", key=True), sourced("B", "https://b.example")],
    )
    samples = recorded.investigators["samples"]
    assert isinstance(samples, dict)
    # s0: a worker's row with a class; s1: an old row, read through its defect types;
    # s2: the auditor's review decides the class
    rows = samples["s0"]["question-labels"][0]["record"]["question_assessments"]  # type: ignore[index]
    rows[0] |= {"epoch_class": "impossible_as_written", "sub_reason": "conflicting_sources"}  # type: ignore[index]
    samples["s1"]["question-labels"] = [  # type: ignore[index]
        {
            **take("DEFECT"),
            "record": {
                "question_assessments": [
                    {
                        "question_id": "s1",
                        "status": "DEFECT",
                        "defect_types": ["incorrect_reference_or_test"],
                    }
                ]
            },
        }
    ]
    recorded.auditor.reviews = [
        Review(
            sample_id="s2",
            check="question-labels",
            label="DEFECT",
            explanation="The scorer credits a hedge.",
            evidence=EVIDENCE,
            epoch_class="false_positive",
            sub_reason="lax_scorer",
        )
    ]
    row = PROSE["error_examples"][0]
    prose = {
        **PROSE,
        "error_examples": [
            {**row, "task": "s2", "notes": "The scorer credits an answer naming two years."},
            {**row, "task": "s1", "notes": "The key misspells the governor's middle name."},
            row,
        ],
    }
    monkeypatch.setattr(epoch, "sandbox", lambda: Box(prose))
    output = asyncio.run(epoch.build(tmp_path, bundle(tmp_path, recorded)))
    page = (output / "epoch_review.md").read_text()
    order = [
        (
            "Defective tasks by class:<br>1. Essentially impossible to correctly answer as "
            "written: 1<br>2. False negative: 1<br>3. False positive: 1 |"
        ),
        "### 1. Essentially impossible to correctly answer as written (1)",
        "| Task | Sub-reason | Notes | Affected logs |",
        "| s0 | Conflicting sources |",
        "### 2. False negative (1)",
        "| s1 | Incorrect or stale key |",
        "### 3. False positive (1)",
        "| s2 | Lax scorer |",
        "Audited without finding a defect (17)",
        "**s0**: Conflicting sources",
    ]
    positions = [page.index(marker) for marker in order]
    assert positions == sorted(positions)
    classes = json.loads((output / "classes.json").read_text())
    assert classes["s1"] == {
        "epoch_class": "false_negative",
        "sub_reason": "incorrect_or_stale_key",
        "check": "question-labels",
        "basis": "legacy",
    }
    assert classes["s2"]["basis"] == "recorded"


def test_a_review_class_must_fit_its_check() -> None:
    with pytest.raises(ValueError, match="sub_reason for false_negative"):
        _findings.validate_draft(
            findings(
                ten(1),
                phase1(),
                reviews=[
                    Review(
                        sample_id="s0",
                        check="question-labels",
                        label="DEFECT",
                        explanation="x",
                        evidence=EVIDENCE,
                        epoch_class="false_negative",
                        sub_reason="lax_scorer",
                    )
                ],
            ).model_dump(mode="json"),
            Register.model_validate(
                {"checks": ["question-labels"], "samples": {"s0": {"question-labels": []}}}
            ),
        )


def test_rows_that_repeat_each_others_words_are_pointed_out(tmp_path: Path, capsys) -> None:
    """An edit by list position wrote one item's Affected logs into the next row."""
    recorded = findings(twenty(2), phase1())
    rows = [
        {
            **PROSE["error_examples"][0],
            "affected_logs": "2 of 49: two received credit without choosing shipbuilding alone",
        },
        {
            **PROSE["error_examples"][0],
            "task": "s1",
            "notes": "The catalogue counts a demo as an EP.",
            "affected_logs": "2 of 49 received credit without choosing shipbuilding alone",
        },
    ]
    prose = tmp_path / "prose.json"
    prose.write_text(json.dumps({**PROSE, "error_examples": rows}))
    source = bundle(tmp_path, recorded)
    assert epoch.main([str(source), "--prose", str(prose), "--out", str(tmp_path / "out")]) == 0
    printed = capsys.readouterr().out
    assert "check: error_examples rows 1 and 2 (s0, s1): affected_logs share" in printed
    assert "received credit without choosing shipbuilding alone" in printed
    warnings = json.loads((tmp_path / "out/verdict.json").read_text())["warnings"]
    assert len(warnings) == 1
    assert _epoch.prose_warnings(_epoch.Prose.model_validate(PROSE), _epoch.template()) == []


def test_sources_name_competing_answers_where_a_reader_can_find_them() -> None:
    from inspect_audit._labels import QuestionAssessment, validate_labels

    row = {
        "question_id": "s0",
        "status": "DEFECT",
        "checks": ["read both catalogues"],
        "evidence": ["https://a.example"],
        "explanation": "Two catalogues give different first EPs.",
        "defect_types": ["missing_information"],
        "epoch_class": "impossible_as_written",
        "sub_reason": "conflicting_sources",
        "sources": [sourced("A", "https://a.example", key=True), sourced("B", "https://b.example")],
    }
    assert validate_labels([row], ["s0"], require_classification=True)[0].sources[1].answer == "B"
    for sources, message in (
        ([sourced("A", "https://a.example"), sourced("a", "https://b.example")], "at least two"),
        ([sourced("A", "https://a.example"), sourced("B", "/tmp/k/page.html")], "local copy"),
        (
            [sourced("A", "https://a.example"), {**sourced("B", "x"), "quote": "word " * 61}],
            "at most 60",
        ),
    ):
        with pytest.raises(ValueError, match=message):
            validate_labels([{**row, "sources": sources}], ["s0"], require_classification=True)
    # rows recorded before sources existed still validate
    legacy = {k: v for k, v in row.items() if k != "sources"}
    assert QuestionAssessment.model_validate(legacy).sources == []


def test_with_no_review_the_workers_majority_stands() -> None:
    def take(run: str, label: str) -> dict[str, Any]:
        return {"by": "investigator", "label": label, "run": run}

    label = _epoch._standing_label  # pyright: ignore[reportPrivateUsage]
    assert label([]) is None
    assert label([take("a", "DEFECT"), take("a", "NO_ISSUE_FOUND")]) == "NO_ISSUE_FOUND"
    three = [take("a", "NO_ISSUE_FOUND"), take("b", "DEFECT"), take("c", "NO_ISSUE_FOUND")]
    assert label(three) == "NO_ISSUE_FOUND"
    assert label([take("a", "NO_ISSUE_FOUND"), take("b", "UNRESOLVED")]) == "DEFECT"
    assert label([take("a", "DEFECT"), take("b", "NO_VERDICT")]) == "DEFECT"
    assert label([*three, {"by": "auditor", "label": "DEFECT"}]) == "DEFECT"


# ------------------------------------------------------------------ the full page

# pages and the staged methodology.md rendered by the publisher before `full_page`
# existed: the default must stay byte for byte what it was
GOLDEN = Path(__file__).parent / "fixtures/epoch_pages"
VERIFIED_PROSE = {
    **PROSE,
    "interpretation": "One score is the share of questions answered right.",
    "task_analysis": "Short factual questions graded against one reference answer.",
    "elicitation_and_scaffolding": "One prompt, no tools.",
}


def golden_cases() -> list[tuple[str, Findings]]:
    return [
        ("incomplete", findings(ten(1), phase1())),
        ("verified", findings(ten(1), phase2(), logs=True)),
    ]


def test_the_default_page_renders_exactly_as_before(tmp_path: Path, monkeypatch) -> None:
    for name, case in golden_cases():
        expected = (GOLDEN / f"{name}.md").read_text()
        text = (GOLDEN / f"{name}.prose.json").read_text()
        for full_page in ((), (False,)):
            prose = _epoch.Prose.model_validate_json(text)
            assert _epoch.review(case, prose, *full_page)["page"] == expected
        # through the publisher, named alone or with its default option
        for spec in ("epoch", {"name": "epoch"}, {"name": "epoch", "full_page": False}):
            (where := tmp_path / f"{name}-{len(str(spec))}").mkdir()
            source = bundle(where, case)
            monkeypatch.setattr(epoch, "sandbox", lambda text=text: Box(json.loads(text)))
            output = asyncio.run(load_publisher(spec).build(tmp_path / "out", source))
            assert (output / "epoch_review.md").read_text() == expected
            assert "full_page" not in json.loads((output / "verdict.json").read_text())
    staged = tmp_path / "analysis"
    load_publisher("epoch").stage(staged)  # pyright: ignore[reportOptionalCall]
    expected = (GOLDEN / "methodology.md").read_text()
    assert (staged / "epoch/methodology.md").read_text() == expected
    assert _epoch.methodology_markdown(_epoch.methodology(), _epoch.template()) == expected


def test_the_full_page_renders_every_block_the_verdict_shows(tmp_path: Path, monkeypatch) -> None:
    source = bundle(tmp_path, findings(ten(1), phase2(), logs=True))
    publisher = load_publisher({"name": "epoch", "full_page": True})
    monkeypatch.setattr(epoch, "sandbox", lambda: Box(VERIFIED_PROSE))
    output = asyncio.run(publisher.build(tmp_path, source))
    page = (output / "epoch_review.md").read_text()
    # the prose first, then the disclaimer, the rubric, the errors and the footnotes
    order = [
        "Verdict: Verified",
        "## Summary",
        "## Methodology",
        "## Interpretation",
        "## Task Analysis",
        "## Elicitation and Scaffolding",
        "## Limitations",
        "## Recommendations",
        "## Disclaimer",
        "without requesting additional information from Google",
        "## Benchmark Review Rubric",
        "## Representative Errors",
        "| s0 | Impossible as written |",
        "Audited without finding a defect (9)",
        "## Footnotes",
        "[^1]: Sample s0",
    ]
    positions = [page.index(marker) for marker in order]
    assert positions == sorted(positions)
    assert all(page.count(marker) == 1 for marker in order if marker.startswith("## "))
    assert json.loads((output / "verdict.json").read_text())["full_page"] is True
    # an Incomplete review's notice follows the errors and precedes the footnotes
    (tmp_path / "phase1").mkdir()
    source = bundle(tmp_path / "phase1", findings(ten(1), phase1()))
    monkeypatch.setattr(epoch, "sandbox", lambda: Box(PROSE))
    page = (asyncio.run(publisher.build(tmp_path, source)) / "epoch_review.md").read_text()
    assert (
        page.index("## Summary")
        < page.index("## Disclaimer")
        < page.index("## Benchmark Review Rubric")
        < page.index("## Representative Errors")
        < page.index("Phase 1 only")
        < page.index("## Footnotes")
    )
    assert "## Interpretation" not in page  # not part of an Incomplete review


def test_a_full_page_refuses_a_missing_or_overlong_required_block(
    tmp_path: Path, monkeypatch
) -> None:
    source = bundle(tmp_path, findings(ten(1), phase2(), logs=True))
    full = load_publisher({"name": "epoch", "full_page": True})
    for missing in ("summary", "task_analysis", "elicitation_and_scaffolding", "recommendations"):
        prose = {k: v for k, v in VERIFIED_PROSE.items() if k != missing}
        if missing == "summary":
            prose["footnotes"] = []
        monkeypatch.setattr(epoch, "sandbox", lambda prose=prose: Box(prose))
        with pytest.raises(ValueError, match=f"Verified review needs the {missing}"):
            asyncio.run(full.build(tmp_path, source))
        if missing != "summary":
            # the default page ends after the opening: the same prose is accepted
            asyncio.run(load_publisher("epoch").build(tmp_path, source))
    long = {**VERIFIED_PROSE, "summary": " ".join(["word"] * 151) + ".[^1]"}
    monkeypatch.setattr(epoch, "sandbox", lambda: Box(long))
    with pytest.raises(ValueError, match="summary"):
        asyncio.run(full.build(tmp_path, source))


def test_the_full_page_is_a_publisher_option(tmp_path: Path, capsys) -> None:
    full = load_publisher({"name": "epoch", "full_page": True})
    assert full.stage is not None
    full.stage(tmp_path / "full")
    staged = (tmp_path / "full/epoch/methodology.md").read_text()
    assert "not rendered" not in staged and "leave them out" not in staged
    assert "### task_analysis (prose; shown for Verified; required for Verified)" in staged
    assert "Write every block marked required for the verdict" in staged
    for spec, message in (
        ({"name": "epoch", "full": True}, "takes no option"),
        ({"name": "latex", "full_page": True}, "takes no option"),
        ({"full_page": True}, "a mapping with a `name`"),
    ):
        with pytest.raises(ValueError, match=message):
            load_publisher(spec)
    with pytest.raises(TypeError, match="full_page"):
        load_publisher({"name": "epoch", "full_page": "yes"})
    # the render command takes it too
    source = bundle(tmp_path, findings(ten(1), phase2(), logs=True))
    prose = tmp_path / "prose.json"
    prose.write_text(json.dumps(VERIFIED_PROSE))
    out = tmp_path / "out"
    assert epoch.main([str(source), "--prose", str(prose), "--out", str(out), "--full-page"]) == 0
    assert "## Task Analysis" in (out / "epoch_review.md").read_text()
    assert epoch.main([str(source), "--prose", str(prose), "--out", str(out)]) == 0
    assert "## Task Analysis" not in (out / "epoch_review.md").read_text()


def test_the_stop_at_the_first_flag_can_be_turned_off(monkeypatch: pytest.MonkeyPatch) -> None:
    flagged = phase1(
        benchmark_consistency=rating(
            "Flag", impact="Scores from the two scorer versions differ by 11 points."
        )
    )
    after = findings(ten(1), flagged | {"elicitation_adequacy": rating("Pass")}, logs=True)
    # the default is the published rubric's stop, from the environment too
    monkeypatch.delenv(_epoch.STOP_AT_FIRST_FLAG, raising=False)
    with pytest.raises(ValueError, match=r"stops at the first Flag.*elicitation_adequacy"):
        _epoch.validate(after)
    # turned off, the rows after the Flag stand; the verdict is still Flawed and says why
    derived = _epoch.validate(after, stop_at_first_flag=False)
    assert derived["verdict"] == "Flawed"
    assert any("rated after the first Flag" in r for r in derived["reasons"])
    monkeypatch.setenv(_epoch.STOP_AT_FIRST_FLAG, "false")
    assert _epoch.validate(after)["verdict"] == "Flawed"
    epoch.validate(after)  # the publisher's submission check reads it the same way
    monkeypatch.setenv(_epoch.STOP_AT_FIRST_FLAG, "maybe")
    with pytest.raises(ValueError, match=_epoch.STOP_AT_FIRST_FLAG):
        _epoch.validate(after)
