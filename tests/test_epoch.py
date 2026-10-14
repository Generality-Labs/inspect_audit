"""The Epoch review: records against a methodology, a verdict by its procedure, a page by a template."""

import json
from pathlib import Path

import pytest

from inspect_audit._epoch import (
    METHODOLOGY,
    TEMPLATE,
    assess,
    derive_verdict,
    measures,
    prepare_publication,
    prepare_review,
    skeleton_assessments,
)
from inspect_audit._report import save_publication, validate_findings
from inspect_audit.methodology import load_methodology
from inspect_audit.records import (
    Assessment,
    Profile,
    Prose,
    Sampling,
    sentences,
    validate_assessments,
)
from inspect_audit.template import load_template, ordinal_date

MINIMUM = ("scoring_integrity", "benchmark_consistency", "elicitation_adequacy", "evaluation_bias")


def evidence(path: str = "/inputs/header.json") -> list[dict[str, str]]:
    return [{"path": path, "location": "eval.model_generate_config"}]


def filled_assessments(**levels: str) -> list[dict]:
    """Every check present; the gate Full and the minimum standard Pass unless overridden."""
    rows = skeleton_assessments(load_methodology(METHODOLOGY))
    for row in rows:
        if row["check"] == "reviewability":
            row.update(level="Full", notes="Logs and gist.", evidence=evidence())
        elif row["check"] in MINIMUM:
            row.update(level="Pass", notes="Checked.", evidence=evidence())
        if row["check"] in levels:
            row["level"] = levels[row["check"]]
    return rows


def row(rows: list[dict], check: str) -> dict:
    return next(r for r in rows if r["check"] == check)


def verified_prose(**overrides) -> dict:
    prose = {
        "summary": "One move per position. Exact match.",
        "methodology": "Read 188 logs. Ran 100 item audits.",
        "interpretation": "The number is the share of positions solved.",
        "task_analysis": "Positions come from engine games.",
        "elicitation_and_scaffolding": "One prompt, no tools.",
        "limitations": ["Single run per model.", "No human baseline."],
        "recommendations": ["Publish the engine version."],
    }
    prose.update(overrides)
    return prose


def flawed_prose() -> dict:
    return {
        "summary": "One move per position. Exact match.",
        "methodology": "Read 188 logs. Ran 100 item audits.",
        "recommendations": ["Bump the version when the grader changes."],
        "representative_errors": ["The answer-reading model changed within one version."],
        "error_examples": [
            {
                "task": "Puzzle 86",
                "error_type": "False negative",
                "notes": "Prefix kept in the extracted answer.",
                "affected_logs": "1 run (GPT-5.4 high)",
            }
        ],
    }


def profile(**overrides) -> dict:
    p = {"benchmark": "Chess Puzzles", "creator": "Epoch AI", "review_date": "2026-10-06"}
    p.update(overrides)
    return p


def coverage_of(defect: int, clean: int, unresolved: int = 0, not_assessed: int = 0) -> dict:
    total = defect + clean + unresolved + not_assessed
    return {
        "denominator": total,
        "counts": {
            "NO_ISSUE_FOUND": clean,
            "DEFECT": defect,
            "UNRESOLVED": unresolved,
            "NOT_ASSESSED": not_assessed,
        },
    }


def workspace(
    tmp_path: Path,
    assessments: list[dict] | None = None,
    prose: dict | None = None,
    coverage: dict | None = None,
    sampling: dict | None = None,
    **profile_overrides,
) -> Path:
    report = tmp_path / "work/report"
    inputs = tmp_path / "inputs"
    report.mkdir(parents=True)
    inputs.mkdir()
    (inputs / "header.json").write_text("{}")
    prepare_review(report, "bench/Chess Puzzles")
    (report / "profile.json").write_text(json.dumps(profile(**profile_overrides)))
    (report / "assessments.json").write_text(json.dumps(assessments or filled_assessments()))
    (report / "prose.json").write_text(json.dumps(prose if prose is not None else verified_prose()))
    (report / "findings.json").write_text("[]")
    if coverage is not None:
        (report / "coverage.json").write_text(json.dumps(coverage))
    if sampling is not None:
        (report / "sampling.json").write_text(json.dumps(sampling))
    return tmp_path


def derived_for(
    rows: list[dict], coverage: dict | None = None, sampling: dict | None = None
) -> dict:
    method = load_methodology(METHODOLOGY)
    assessments = validate_assessments([Assessment.model_validate(r) for r in rows], method)
    s = Sampling.model_validate(sampling) if sampling else None
    return derive_verdict(method, assessments, measures(method, assessments, coverage, s))


# ------------------------------------------------------------------ the records


def test_the_skeleton_has_every_check_unassessed_and_validates() -> None:
    method = load_methodology(METHODOLOGY)
    rows = skeleton_assessments(method)
    assert [r["check"] for r in rows] == list(method.check_ids)
    assert {r["level"] for r in rows} == {"Not Reviewed"}
    assert derived_for(rows)["verdict"] == "Incomplete"


def test_assessments_are_a_closed_vocabulary_with_evidence() -> None:
    method = load_methodology(METHODOLOGY)

    def rejects(change, match: str) -> None:
        rows = filled_assessments()
        change(rows)
        with pytest.raises(ValueError, match=match):
            validate_assessments([Assessment.model_validate(r) for r in rows], method)

    rejects(lambda r: r.pop(), "every check of epoch-rubric@1 exactly once")
    rejects(lambda r: r[1].update(check="vibes"), "every check")
    rejects(
        lambda r: row(r, "scoring_integrity").update(level="Maybe"),
        "level must be one of Pass, Flag",
    )
    rejects(
        lambda r: row(r, "scoring_integrity").update(evidence=[]), "needs at least one evidence"
    )
    rejects(
        lambda r: row(r, "resource_adequacy").update(level="Fine"),
        "level must be one of Sufficient",
    )
    rejects(
        lambda r: row(r, "contamination").update(level="Assessed", evidence=evidence()),
        "needs fields as_of",
    )
    rejects(lambda r: row(r, "contamination").update(fields={"x": 1}), "unknown fields x")
    rejects(lambda r: row(r, "reviewability").update(evidence=[]), "needs at least one evidence")
    rejects(
        lambda r: row(r, "evaluation_bias").update(
            measure={"name": "sampled_defect_rate", "numerator": 1, "denominator": 2}
        ),
        "belong to a check with a threshold",
    )
    rejects(
        lambda r: row(r, "scoring_integrity").update(
            measure={"name": "other", "numerator": 1, "denominator": 2}
        ),
        "measure must be sampled_defect_rate",
    )
    rows = filled_assessments()
    row(rows, "contamination").update(
        level="Assessed",
        fields={"as_of": "2026-10-06", "tasks_public_pct": 100, "solutions_public_pct": 100},
        evidence=evidence(),
    )
    validate_assessments([Assessment.model_validate(r) for r in rows], method)
    with pytest.raises(ValueError):
        Assessment.model_validate({**rows[0], "extra": 1})
    with pytest.raises(ValueError, match="review_date"):
        Profile.model_validate(profile(review_date="6 October 2026"))


def test_a_flag_needs_its_impact_and_a_pass_has_none() -> None:
    rows = filled_assessments(elicitation_adequacy="Flag")
    with pytest.raises(ValueError, match="elicitation_adequacy: a Flag needs `impact`"):
        derived_for(rows)
    row(rows, "elicitation_adequacy")["impact"] = "Eight rows score 0-16%; their best rows 20-35%."
    assert derived_for(rows)["verdict"] == "Flawed"
    rows = filled_assessments()
    row(rows, "scoring_integrity")["impact"] = "Stray."
    with pytest.raises(ValueError, match="impact belongs to a Flag"):
        derived_for(rows)
    rows = filled_assessments(scoring_integrity="Flag")
    row(rows, "scoring_integrity")["impact"] = " ".join(["w"] * 61)
    with pytest.raises(ValueError, match="impact: 61 words, limit 60"):
        derived_for(rows)


def test_note_limits_come_from_the_methodology() -> None:
    rows = filled_assessments()
    row(rows, "scoring_integrity")["notes"] = " ".join(["word"] * 81)
    with pytest.raises(ValueError, match="scoring_integrity notes: 81 words, limit 80"):
        derived_for(rows)
    rows = filled_assessments()
    row(rows, "scoring_integrity")["notes"] = "One. Two. Three. Four. Five."
    with pytest.raises(ValueError, match="5 sentences, limit 4"):
        derived_for(rows)
    assert sentences("Scores range from 0.54 to 0.72. The e.g. case is fine.") == 2


# ------------------------------------------------------------------ the verdict


def test_the_procedure_is_applied_in_order() -> None:
    assert derived_for(filled_assessments())["verdict"] == "Verified"
    gated = filled_assessments(reviewability="Inadequate", **dict.fromkeys(MINIMUM, "Flag"))
    for c in MINIMUM:
        row(gated, c)["impact"] = "Ten points."  # a flag behind a closed gate changes nothing
    assert derived_for(gated)["verdict"] == "NEI"
    flawed = filled_assessments(benchmark_consistency="Flag")
    row(flawed, "benchmark_consistency")["impact"] = "Scores differ by 9 points."
    derived = derived_for(flawed)
    assert derived["verdict"] == "Flawed" and "Benchmark consistency" in derived["reasons"][0]
    partial = filled_assessments(elicitation_adequacy="Not Reviewed")
    row(partial, "elicitation_adequacy")["evidence"] = []
    assert derived_for(partial)["verdict"] == "Incomplete"
    assert derived["methodology"] == "epoch-rubric@1"


def test_the_measure_comes_from_coverage_and_must_agree_with_the_level() -> None:
    rows = filled_assessments()
    # 5 defects among 20 resolved labels = 25%: a Pass contradicts the threshold
    with pytest.raises(ValueError, match="at or above the 20% threshold"):
        derived_for(rows, coverage_of(defect=5, clean=15, unresolved=3, not_assessed=72))
    row(rows, "scoring_integrity").update(
        level="Flag", impact="Five of twenty inspected questions are defective."
    )
    derived = derived_for(rows, coverage_of(defect=5, clean=15, unresolved=3, not_assessed=72))
    m = derived["measures"]["scoring_integrity"]
    assert derived["verdict"] == "Flawed"
    assert (
        m["numerator"],
        m["denominator"],
        m["beside"]["unresolved"],
        m["beside"]["population"],
    ) == (5, 20, 3, 95)
    assert (
        m["exceeded"] is True
        and m["source"] == "coverage.json"
        and m["name"] == "sampled_defect_rate"
    )
    # below the threshold a Flag needs its reason written down, and is then honoured
    with pytest.raises(ValueError, match="below the 20% threshold"):
        derived_for(rows, coverage_of(defect=1, clean=19))
    row(rows, "scoring_integrity")["threshold_override_reason"] = (
        "a scorer bug corrupts grading at scale"
    )
    derived = derived_for(rows, coverage_of(defect=1, clean=19))
    assert derived["verdict"] == "Flawed" and any("overridden" in r for r in derived["reasons"])


def test_without_coverage_the_assessment_may_carry_its_own_measure() -> None:
    rows = filled_assessments(scoring_integrity="Flag")
    row(rows, "scoring_integrity").update(
        impact="Four of ten inspected questions.",
        measure={"name": "sampled_defect_rate", "numerator": 4, "denominator": 10},
    )
    derived = derived_for(rows)
    m = derived["measures"]["scoring_integrity"]
    assert m["source"] == "assessments.json" and m["value"] == pytest.approx(0.4)
    assert derived["verdict"] == "Flawed"


def test_a_purposive_sample_does_not_measure_prevalence() -> None:
    rows = filled_assessments()
    purposive = {"population": 100, "sample": 20, "strategy": "purposive"}
    derived = derived_for(rows, coverage_of(defect=10, clean=10), purposive)  # 50% of a chosen set
    # the counts cannot clear the threshold, and a Pass on them is not a Pass
    assert derived["verdict"] == "Incomplete"
    assert derived["measures"]["scoring_integrity"]["value"] is None
    assert "passes without an admissible measure" in derived["reasons"][0]
    assert "purposive sample" in derived["reasons"][0]
    random = {"population": 100, "sample": 20, "strategy": "random", "seed": "20261006"}
    with pytest.raises(ValueError, match="at or above"):
        derived_for(rows, coverage_of(defect=10, clean=10), random)
    # a Flag on a measured impact still stands on a purposive sample
    flagged = filled_assessments(scoring_integrity="Flag")
    row(flagged, "scoring_integrity")["impact"] = "Two of four recorded submissions were misgraded."
    assert derived_for(flagged, coverage_of(defect=10, clean=10), purposive)["verdict"] == "Flawed"
    # and a draw made elsewhere is recorded as its design, with the drawer named
    elsewhere = {**random, "seed": "", "drawn_by": "independent reviewer: Epoch AI"}
    assert Sampling.model_validate(elsewhere).for_prevalence
    with pytest.raises(ValueError, match="at or above"):
        derived_for(rows, coverage_of(defect=10, clean=10), elsewhere)


# ------------------------------------------------------------------ the prose


def test_prose_blocks_follow_the_verdict_and_the_template_limits() -> None:
    template = load_template(TEMPLATE)
    Prose.model_validate(verified_prose()).validate_against(template, "Verified")
    with pytest.raises(ValueError, match="representative_errors is not part of a Verified review"):
        Prose.model_validate(verified_prose(representative_errors=["Oops."])).validate_against(
            template, "Verified"
        )
    with pytest.raises(ValueError, match="a Verified review needs the interpretation block"):
        Prose.model_validate(verified_prose(interpretation="")).validate_against(
            template, "Verified"
        )
    with pytest.raises(
        ValueError, match="a Flawed review needs the representative_errors, error_examples block"
    ):
        Prose.model_validate(verified_prose()).validate_against(template, "Flawed")
    with pytest.raises(ValueError, match="needs the recommendations block"):
        Prose.model_validate(verified_prose(recommendations=[])).validate_against(
            template, "Verified"
        )
    Prose.model_validate(flawed_prose()).validate_against(template, "Flawed")
    with pytest.raises(ValueError, match="interpretation is not part of a Flawed review"):
        Prose.model_validate({**flawed_prose(), "interpretation": "Kept."}).validate_against(
            template, "Flawed"
        )
    Prose.model_validate(
        {"summary": "S.", "methodology": "M.", "limitations": ["Why."]}
    ).validate_against(template, "NEI")
    with pytest.raises(ValueError, match="unknown blocks verdict"):
        Prose.model_validate({**verified_prose(), "verdict": "Verified"}).validate_against(
            template, "Verified"
        )
    for block, limit in (("summary", 150), ("methodology", 125), ("interpretation", 300)):
        with pytest.raises(ValueError, match=f"{block}: {limit + 1} words"):
            Prose.model_validate(
                verified_prose(**{block: " ".join(["w"] * (limit + 1))})
            ).validate_against(template, "Verified")
    with pytest.raises(ValueError, match="paragraph 1 has 6 sentences"):
        Prose.model_validate(
            verified_prose(summary="One. Two. Three. Four. Five. Six.")
        ).validate_against(template, "Verified")
    Prose.model_validate(
        verified_prose(summary="One. Two. Three.\n\nFour. Five. Six.")
    ).validate_against(template, "Verified")
    with pytest.raises(ValueError, match="limitations bullet 1: 4 sentences, limit 3"):
        Prose.model_validate(
            verified_prose(limitations=["One. Two. Three. Four."])
        ).validate_against(template, "Verified")
    bad = flawed_prose()
    bad["error_examples"][0]["notes"] = " ".join(["w"] * 31)
    with pytest.raises(ValueError, match="notes over 30 words"):
        Prose.model_validate(bad).validate_against(template, "Flawed")
    bad = flawed_prose()
    bad["error_examples"][0].pop("affected_logs")
    with pytest.raises(ValueError, match="columns are task, error_type, notes, affected_logs"):
        Prose.model_validate(bad).validate_against(template, "Flawed")


# ------------------------------------------------------------------ the workspace


def test_prepare_stages_the_methodology_template_schemas_and_records(tmp_path: Path) -> None:
    report = tmp_path / "report"
    report.mkdir()
    prepare_review(report, "Chess Puzzles")
    assert json.loads((report / "format.json").read_text()) == {
        "format": "epoch",
        "methodology": "epoch-rubric@1",
        "template": "epoch-review",
    }
    assert json.loads((report / "profile.json").read_text())["benchmark"] == "Chess Puzzles"
    assert json.loads((report / "prose.json").read_text()) == {}
    assert [r["check"] for r in json.loads((report / "assessments.json").read_text())][:2] == [
        "reviewability",
        "scoring_integrity",
    ]
    schema = json.loads((report / "findings.schema.json").read_text())
    assert schema["properties"]["check"]["enum"] == list(load_methodology(METHODOLOGY).check_ids)
    assert "section" not in schema["properties"]
    assert set(json.loads((report / "prose.schema.json").read_text())["properties"]) == {
        b.id for b in load_template(TEMPLATE).blocks
    }
    assert (report / "methodology.yaml").is_file() and (report / "template.yaml").is_file()
    text = (report / "methodology.md").read_text()
    assert "### `scoring_integrity`: Scoring" in text and "sampled_defect_rate >= 0.20" in text
    assert "- `summary` (prose, at most 150 words" in text
    # a second prepare (a resume) keeps the agent's records
    (report / "assessments.json").write_text(json.dumps(filled_assessments()))
    prepare_review(report, "Chess Puzzles")
    assert json.loads((report / "assessments.json").read_text())[0]["level"] == "Full"


def test_findings_are_filed_under_checks_in_the_epoch_format(tmp_path: Path) -> None:
    root = workspace(tmp_path)
    finding = {
        "id": "F1",
        "check": "scoring_integrity",
        "claim": "The extractor changed from gemini-2.0-flash to gpt-5.6-terra",
        "status": "supported",
        "origin": "historical",
        "evidence": evidence(),
        "reproduce": "read_eval_log(header_only=True).eval.model_roles",
        "limitations": "",
    }
    register = root / "work/report/findings.json"
    register.write_text(json.dumps([finding]))
    assert validate_findings(root)[0].check == "scoring_integrity"
    register.write_text(json.dumps([{**finding, "check": "grading"}]))  # a GL section
    with pytest.raises(ValueError, match="check must be one of"):
        validate_findings(root)
    register.write_text(json.dumps([{**finding, "check": "", "section": "scoring_integrity"}]))
    with pytest.raises(ValueError, match="check must be one of"):
        validate_findings(root)


def test_a_verified_review_renders_the_page(tmp_path: Path) -> None:
    rows = filled_assessments()
    row(rows, "contamination").update(
        level="Assessed",
        fields={"as_of": "2026-10-06", "tasks_public_pct": 100, "solutions_public_pct": 0},
        evidence=evidence(),
    )
    row(rows, "statistical_adequacy").update(
        level="Known", fields={"runs_per_model": 1}, evidence=evidence()
    )
    root = workspace(tmp_path, rows, coverage=coverage_of(defect=6, clean=94))
    derived = prepare_publication(root, [])
    assert derived["verdict"] == "Verified" and derived["template"] == "epoch-review"
    md = (root / "work/report/epoch_review.md").read_text()
    head = md.splitlines()[:6]
    assert head[0] == "# Chess Puzzles Review"
    assert head[2].startswith("Benchmark: Chess Puzzles")
    assert head[3].startswith("Benchmark creator: Epoch AI")
    assert head[4].startswith("Verdict: Verified")
    assert head[5] == "Review date: 6th October 2026"
    for heading in (
        "## Summary",
        "## Methodology",
        "## Interpretation",
        "## Task Analysis",
        "## Elicitation and Scaffolding",
        "## Limitations",
        "## Recommendations",
        "## Benchmark Review Rubric",
        "### 1. Reviewability",
        "### 2. Scoring",
        "### 3. Evaluation Quality",
        "## Disclaimer",
    ):
        assert heading in md, heading
    assert "## Representative Errors" not in md
    assert "While none of these limitations cross our threshold for a Flawed verdict" in md
    assert "- Single run per model." in md and "- Publish the engine version." in md
    assert "Scoring prevalence: 6 of 100 inspected tasks (6%); threshold 20%." in md
    # the form: every level row printed, notes only on the chosen one, choice in bold
    assert "| **Full** | All tasks and scoring logic inspectable" in md
    assert "| Partial | A representative sample" in md and "| Inadequate | Limited, biased" in md
    assert md.count("Logs and gist.") == 1
    assert "**Pass**<br>Flag [stop → Flawed]<br>Not Reviewed" in md
    assert (
        "Sufficient<br>Constraining<br>Unreasonably constraining<br>Unknown<br>**Not Reviewed**"
        in md
    )
    assert (
        "**As of 2026-10-06: 100% of tasks public<br>0% of solutions public**<br>Not Reviewed" in md
    )
    assert "**1 runs/model**<br>Unknown<br>Not Reviewed" in md
    assert (
        "Poor implementation (Unrepresentative set of tasks, unreasonable set of participants)<br>Not established<br>**Not reviewed**"
        in md
    )
    assert "| Defect Class | Examples | Default Threshold for Flawed | Status | Notes |" in md
    assert "• Scorer, instructions, or ground truth changed without a version bump" in md
    assert "additional information from Epoch AI to provide impartial feedback" in md
    destination = save_publication(root)
    assert (destination / "epoch_review.md").is_file() and (
        destination / "assessments.json"
    ).is_file()
    assert json.loads((destination / "verdict.json").read_text())["verdict"] == "Verified"
    assert not (destination / "report.pdf").exists()


def test_a_flawed_review_renders_representative_errors(tmp_path: Path) -> None:
    rows = filled_assessments(benchmark_consistency="Flag")
    row(rows, "benchmark_consistency")["impact"] = (
        "Rows graded by the two models differ by 9 points."
    )
    root = workspace(tmp_path, rows, flawed_prose(), coverage_of(defect=8, clean=92))
    derived = prepare_publication(
        root, [{"id": "F1", "check": "benchmark_consistency", "status": "supported"}]
    )
    assert derived["verdict"] == "Flawed"
    assert derived["findings"] == [
        {"id": "F1", "check": "benchmark_consistency", "status": "supported"}
    ]
    md = (root / "work/report/epoch_review.md").read_text()
    assert (
        "Verdict: Flawed" in md
        and "The following issues cross our threshold for a Flawed verdict." in md
    )
    assert "**Impact:** Rows graded by the two models differ by 9 points. Checked." in md
    assert "**Flag [stop → Flawed]**" in md and "- Bump the version when the grader changes." in md
    assert "| Task | Error type | Notes | Affected logs |" in md
    assert (
        "| Puzzle 86 | False negative | Prefix kept in the extracted answer. | 1 run (GPT-5.4 high) |"
        in md
    )
    assert "## Interpretation" not in md and "## Limitations" not in md


def test_publication_fills_the_date_merges_sampling_and_refuses_the_wrong_prose(
    tmp_path: Path,
) -> None:
    sampling = {"population": 100, "sample": 100, "strategy": "census"}
    root = workspace(
        tmp_path, coverage=coverage_of(defect=1, clean=99), sampling=sampling, review_date=""
    )
    prepare_publication(root, [])
    assert json.loads((root / "work/report/profile.json").read_text())["review_date"]
    assert (
        json.loads((root / "work/report/coverage.json").read_text())["sampling"]["strategy"]
        == "census"
    )
    root = workspace(tmp_path / "b", prose=verified_prose(interpretation=""))
    with pytest.raises(ValueError, match="needs the interpretation block"):
        prepare_publication(root, [])
    root = workspace(tmp_path / "c", creator="")
    with pytest.raises(ValueError, match=r"profile\.creator is empty"):
        prepare_publication(root, [])
    root = workspace(tmp_path / "d", coverage=coverage_of(defect=10, clean=10))
    with pytest.raises(ValueError, match="scoring_integrity is Pass"):
        prepare_publication(root, [])


def test_assess_renders_a_bundle_without_a_workspace(tmp_path: Path) -> None:
    root = workspace(tmp_path)
    derived = assess(root / "work/report")
    assert derived["verdict"] == "Verified" and derived["page"].startswith("# Chess Puzzles Review")


def test_ordinal_dates() -> None:
    assert ordinal_date("2026-10-06") == "6th October 2026"
    assert ordinal_date("2026-10-01") == "1st October 2026"
    assert ordinal_date("2026-10-22") == "22nd October 2026"
    assert ordinal_date("2026-10-13") == "13th October 2026"


def test_the_gl_format_is_untouched_by_default(tmp_path: Path) -> None:
    from inspect_audit._report import GL_SECTIONS, allowed_sections, report_format

    report = tmp_path / "work/report"
    report.mkdir(parents=True)
    assert report_format(report) == "gl"
    assert allowed_sections(report) == GL_SECTIONS
    (report / "format.json").write_text(json.dumps({"format": "epoch"}))
    assert allowed_sections(report) == load_methodology(METHODOLOGY).check_ids
    (report / "format.json").write_text(json.dumps({"format": "html"}))
    with pytest.raises(ValueError, match="unknown report format"):
        report_format(report)


def test_investigate_prepares_the_epoch_workspace_and_prompt(tmp_path: Path) -> None:
    import subprocess

    from inspect_audit import prompts
    from inspect_audit._investigate import ASSETS, WRITING_SKILLS, investigate

    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    (repo / "task.py").write_text("# benchmark source\n")
    env = {
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@x",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@x",
    }
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
    subprocess.run(
        ["git", "-C", str(repo), "commit", "-q", "-m", "init"],
        check=True,
        env={**__import__("os").environ, **env},
    )
    target = investigate(
        str(repo), output_dir=str(tmp_path / "runs"), execution="local", report="epoch"
    )
    root = Path(target.metadata["investigation_dir"])
    report = root / "work/report"
    assert target.metadata["report"] == "epoch"
    assert "epoch_report" in target.metadata["capabilities"]
    assert json.loads((report / "format.json").read_text())["methodology"] == "epoch-rubric@1"
    for name in (
        "methodology.md",
        "methodology.yaml",
        "template.yaml",
        "profile.json",
        "assessments.json",
        "prose.json",
    ):
        assert (report / name).is_file(), name
    assert not (report / "Findings.tex").exists() and not (report / "review.json").exists()
    seed = json.loads((root / "inputs/seed.json").read_text())
    assert seed["report"] == {"format": "epoch", "writing_skill": "writing-epoch"}
    prompt = prompts.investigate_prompt("epoch")
    assert "assessments.json" in prompt and "prose.json" in prompt and "Findings.tex" not in prompt
    assert "Findings.tex" in prompts.investigate_prompt("gl")
    for name in WRITING_SKILLS.values():
        assert (ASSETS / "skills" / name / "SKILL.md").is_file()
    with pytest.raises(ValueError, match="cannot resume as 'gl'"):
        investigate(str(repo), resume=str(root), execution="local", report="gl")
    with pytest.raises(ValueError, match="report must be one of"):
        investigate(str(repo), output_dir=str(tmp_path / "runs2"), execution="local", report="html")


def test_the_writing_skill_names_the_records_and_the_template_names_every_verdict() -> None:
    from inspect_audit._investigate import ASSETS

    skill = (ASSETS / "skills/writing-epoch/SKILL.md").read_text()
    for name in (
        "profile.json",
        "assessments.json",
        "prose.json",
        "sampling.json",
        "findings.json",
        "methodology.md",
    ):
        assert name in skill, name
    assert "review.json" not in skill
    template = load_template(TEMPLATE)
    method = load_methodology(METHODOLOGY)
    template.check_methodology(method)
    for verdict in method.verdict.scale:
        assert any(b.required_for(verdict) for b in template.blocks), verdict


FIXTURES = Path(__file__).parent / "fixtures/epoch"


@pytest.mark.parametrize(
    ("name", "verdict"), [("chess-opus55", "Verified"), ("chess-astra", "Flawed")]
)
def test_converted_bundles_render_their_golden_pages(name: str, verdict: str) -> None:
    """Two real investigations, converted from review.json: the records still render the same page."""
    derived = assess(FIXTURES / name)
    assert derived["verdict"] == verdict
    assert derived["page"] == (FIXTURES / name / "epoch_review.md").read_text()
    assert json.loads((FIXTURES / name / "verdict.json").read_text())["verdict"] == verdict
