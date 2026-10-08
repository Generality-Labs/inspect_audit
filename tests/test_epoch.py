"""The Epoch review as a report format: records in, derived verdict and markdown out."""

import json
from pathlib import Path

import pytest

from inspect_audit import _epoch
from inspect_audit._epoch import (
    ERRORS_LEAD,
    LIMITATIONS_LEAD,
    MINIMUM_STANDARD,
    QUALITY,
    SECTIONS,
    WORD_LIMITS,
    Review,
    check_narrative,
    derive_verdict,
    ordinal_date,
    prepare_publication,
    prepare_review,
    sentences,
    skeleton,
)
from inspect_audit._report import save_publication, validate_findings


def evidence(path: str = "/inputs/header.json") -> list[dict[str, str]]:
    return [{"path": path, "location": "eval.model_generate_config"}]


def filled(**overrides) -> dict:
    """A complete Verified review, to be bent by each test."""
    review = skeleton("bench/Chess Puzzles")
    review["benchmark_creator"] = "Epoch AI"
    review["review_date"] = "2026-10-06"
    review["reviewability"] = {"level": "Full", "notes": "Logs and gist.", "evidence": evidence()}
    for row in review["minimum_standard"]:
        row.update(status="Pass", notes="Checked.", evidence=evidence())
    for row in review["quality"]:
        row.update(status="Not Reviewed")
    review.update(
        summary="One move per position. Exact match.",
        methodology="Read 188 logs. Ran 100 item audits.",
        interpretation="The number is the share of positions solved.",
        task_analysis="Positions come from engine games.",
        elicitation_and_scaffolding="One prompt, no tools.",
        limitations=["Single run per model.", "No human baseline."],
    )
    review.update(overrides)
    return review


def workspace(tmp_path: Path, review: dict, coverage: dict | None = None) -> Path:
    report = tmp_path / "work/report"
    inputs = tmp_path / "inputs"
    report.mkdir(parents=True)
    inputs.mkdir()
    (inputs / "header.json").write_text("{}")
    prepare_review(report, "bench/Chess Puzzles")
    (report / "review.json").write_text(json.dumps(review))
    (report / "findings.json").write_text("[]")
    if coverage is not None:
        (report / "coverage.json").write_text(json.dumps(coverage))
    return tmp_path


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


def test_skeleton_has_every_row_unassessed_and_validates() -> None:
    review = Review.model_validate(skeleton("bench/Chess Puzzles"))
    assert review.benchmark == "Chess Puzzles"
    assert [r.id for r in review.minimum_standard] == list(MINIMUM_STANDARD)
    assert [r.id for r in review.quality] == list(QUALITY)
    assert review.reviewability.level == "Not Reviewed"
    assert derive_verdict(review, None)["verdict"] == "Incomplete"


def test_the_stop_rules_are_applied_in_order() -> None:
    review = Review.model_validate(filled())
    assert derive_verdict(review, None)["verdict"] == "Verified"

    gated = filled()
    gated["reviewability"]["level"] = "Inadequate"
    for row in gated["minimum_standard"]:
        row.update(status="Flag")  # a flag behind a closed gate changes nothing
    assert derive_verdict(Review.model_validate(gated), None)["verdict"] == "NEI"

    flawed = filled()
    flawed["minimum_standard"][1]["status"] = "Flag"
    derived = derive_verdict(Review.model_validate(flawed), None)
    assert derived["verdict"] == "Flawed"
    assert "Benchmark consistency" in derived["reasons"][0]

    partial = filled()
    partial["minimum_standard"][2].update(status="Not Reviewed", evidence=[])
    assert derive_verdict(Review.model_validate(partial), None)["verdict"] == "Incomplete"


def test_prevalence_comes_from_coverage_and_must_agree_with_the_status() -> None:
    review = Review.model_validate(filled())
    # 5 defects among 20 resolved labels = 25%: a Pass contradicts the default threshold
    with pytest.raises(ValueError, match="at or above the 20% threshold"):
        derive_verdict(review, coverage_of(defect=5, clean=15, unresolved=3, not_assessed=72))
    scoring = next(r for r in review.minimum_standard if r.id == "scoring")
    scoring.status = "Flag"
    derived = derive_verdict(review, coverage_of(defect=5, clean=15, unresolved=3, not_assessed=72))
    prev = derived["scoring_prevalence"]
    assert derived["verdict"] == "Flawed"
    assert (prev["with_defect"], prev["inspected"], prev["unresolved"], prev["population"]) == (
        5,
        20,
        3,
        95,
    )
    assert prev["exceeded"] is True and prev["source"] == "coverage.json"
    # below the threshold a Flag needs its reason written down, and is then honoured
    with pytest.raises(ValueError, match="below the 20% threshold"):
        derive_verdict(review, coverage_of(defect=1, clean=19))
    scoring.threshold_override_reason = "a scorer bug corrupts grading at scale"
    derived = derive_verdict(review, coverage_of(defect=1, clean=19))
    assert derived["verdict"] == "Flawed"
    assert any("overridden" in r for r in derived["reasons"])


def test_without_coverage_the_row_may_carry_its_own_counts() -> None:
    review = filled()
    review["minimum_standard"][0].update(status="Flag", inspected=10, with_defect=4)
    derived = derive_verdict(Review.model_validate(review), None)
    assert derived["scoring_prevalence"]["source"] == "review.json"
    assert derived["scoring_prevalence"]["rate"] == pytest.approx(0.4)
    review["minimum_standard"][1].update(inspected=1, with_defect=0)
    with pytest.raises(ValueError, match="belong to the scoring row"):
        Review.model_validate(review)


def test_rows_are_closed_vocabularies_with_evidence() -> None:
    for change in [
        lambda r: r["minimum_standard"].pop(),
        lambda r: r["minimum_standard"].__setitem__(0, {**r["minimum_standard"][0], "id": "vibes"}),
        lambda r: r["minimum_standard"][0].update(status="Maybe"),
        lambda r: r["minimum_standard"][0].update(evidence=[]),
        lambda r: r["quality"][0].update(status="Fine"),
        lambda r: r["quality"][2].update(status="Assessed", evidence=evidence()),  # fields missing
        lambda r: r["quality"][2].update(
            fields={
                "as_of": "2026-10",
                "tasks_public_pct": 100,
                "solutions_public_pct": 100,
                "x": 1,
            }
        ),
        lambda r: r["reviewability"].update(evidence=[]),
        lambda r: r.update(extra="field"),
        lambda r: r.update(review_date="6 October 2026"),
    ]:
        review = filled()
        change(review)
        with pytest.raises(ValueError):
            Review.model_validate(review)
    ok = filled()
    ok["quality"][2].update(
        status="Assessed",
        fields={"as_of": "2026-10-06", "tasks_public_pct": 100, "solutions_public_pct": 100},
        evidence=evidence(),
    )
    Review.model_validate(ok)


def test_prepare_stages_schema_skeleton_and_epoch_findings_sections(tmp_path: Path) -> None:
    report = tmp_path / "report"
    report.mkdir()
    prepare_review(report, "Chess Puzzles")
    assert json.loads((report / "format.json").read_text()) == {"format": "epoch"}
    assert json.loads((report / "review.json").read_text())["benchmark"] == "Chess Puzzles"
    schema = json.loads((report / "findings.schema.json").read_text())
    assert schema["properties"]["section"]["enum"] == list(SECTIONS)
    assert (
        "minimum_standard" in json.loads((report / "review.schema.json").read_text())["properties"]
    )
    # a second prepare (a resume) keeps the agent's review
    (report / "review.json").write_text(json.dumps(filled()))
    prepare_review(report, "Chess Puzzles")
    assert json.loads((report / "review.json").read_text())["reviewability"]["level"] == "Full"


def test_findings_are_filed_under_review_rows_in_the_epoch_format(tmp_path: Path) -> None:
    root = workspace(tmp_path, filled())
    finding = {
        "id": "F1",
        "section": "scoring",
        "claim": "The extractor changed from gemini-2.0-flash to gpt-5.6-terra",
        "status": "supported",
        "origin": "historical",
        "evidence": evidence(),
        "reproduce": "read_eval_log(header_only=True).eval.model_roles",
        "limitations": "",
    }
    register = root / "work/report/findings.json"
    register.write_text(json.dumps([finding]))
    assert validate_findings(root)[0].section == "scoring"
    register.write_text(json.dumps([{**finding, "section": "grading"}]))  # a GL section
    with pytest.raises(ValueError, match="section must be one of"):
        validate_findings(root)


def test_length_budgets_are_enforced_on_the_records() -> None:
    review = filled()
    review["minimum_standard"][0]["notes"] = " ".join(["word"] * 81)
    with pytest.raises(ValueError, match="scoring notes: 81 words"):
        Review.model_validate(review)
    for name, limit in WORD_LIMITS.items():
        review = filled()
        review[name] = " ".join(["word"] * (limit + 1))
        with pytest.raises(ValueError, match=f"{name}: {limit + 1} words"):
            Review.model_validate(review)
    review = filled()
    review["summary"] = "One. Two. Three. Four. Five. Six."
    with pytest.raises(ValueError, match="paragraph 1 has 6 sentences"):
        Review.model_validate(review)
    review = filled()
    review["summary"] = "One. Two. Three.\n\nFour. Five. Six."  # two paragraphs are fine
    Review.model_validate(review)
    review = filled()
    review["limitations"] = ["One. Two. Three. Four."]
    with pytest.raises(ValueError, match="limitations: bullet 1 has 4 sentences"):
        Review.model_validate(review)
    assert sentences("Scores range from 0.54 to 0.72. The e.g. case is fine.") == 2


def flawed_narrative() -> dict:
    return {
        "interpretation": "",
        "task_analysis": "",
        "elicitation_and_scaffolding": "",
        "limitations": [],
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


def test_the_narrative_follows_the_verdict() -> None:
    review = Review.model_validate(filled())
    check_narrative(review, "Verified")
    with pytest.raises(ValueError, match="representative errors belong to a Flawed review"):
        check_narrative(Review.model_validate(filled(representative_errors=["Oops."])), "Verified")
    with pytest.raises(ValueError, match="missing or empty: interpretation"):
        check_narrative(Review.model_validate(filled(interpretation="")), "Verified")
    with pytest.raises(ValueError, match="benchmark_creator is empty"):
        check_narrative(Review.model_validate(filled(benchmark_creator="")), "Verified")
    with pytest.raises(ValueError, match="representative_errors, error_examples"):
        check_narrative(review, "Flawed")
    check_narrative(Review.model_validate(filled(**flawed_narrative())), "Flawed")
    mixed = filled(**{**flawed_narrative(), "interpretation": "Kept by mistake."})
    with pytest.raises(ValueError, match="move interpretation"):
        check_narrative(Review.model_validate(mixed), "Flawed")
    check_narrative(
        Review.model_validate(
            filled(interpretation="", task_analysis="", elicitation_and_scaffolding="")
        ),
        "NEI",
    )


def test_a_verified_review_renders_the_reader_sections(tmp_path: Path) -> None:
    review = filled()
    review["quality"][2].update(
        status="Assessed",
        fields={"as_of": "2026-10-06", "tasks_public_pct": 100, "solutions_public_pct": 0},
        evidence=evidence(),
    )
    review["quality"][5].update(status="Known", fields={"runs_per_model": 1}, evidence=evidence())
    root = workspace(tmp_path, review, coverage_of(defect=6, clean=94))
    derived = prepare_publication(root, [])
    assert derived["verdict"] == "Verified"
    md = (root / "work/report/epoch_review.md").read_text()
    head = md.splitlines()[:6]
    assert head[0] == "# Chess Puzzles Review"
    assert head[2].startswith("Benchmark: Chess Puzzles")
    assert head[3].startswith("Benchmark creator: Epoch AI")
    assert head[4].startswith("Verdict: Verified")
    assert head[5].startswith("Review date: 6th October 2026")
    for heading in (
        "## Summary",
        "## Methodology",
        "## Interpretation",
        "## Task Analysis",
        "## Elicitation and Scaffolding",
        "## Limitations",
        "## Review Rubric",
        "## Evidence",
    ):
        assert heading in md, heading
    assert "## Representative Errors" not in md
    assert LIMITATIONS_LEAD in md and "- Single run per model." in md
    assert "Scoring prevalence: 6 of 100 inspected questions (6%); threshold 20%." in md
    assert "As of 2026-10-06: 100% of tasks public, 0% of solutions public" in md
    assert "1 runs/model" in md
    assert "| Full | Logs and gist. |" in md  # evidence is not inside the tables
    assert "- Scoring: `/inputs/header.json` (eval.model_generate_config)" in md
    destination = save_publication(root)
    assert (destination / "epoch_review.md").is_file()
    assert json.loads((destination / "verdict.json").read_text())["verdict"] == "Verified"
    assert not (destination / "report.pdf").exists()


def test_a_flawed_review_renders_representative_errors(tmp_path: Path) -> None:
    review = filled(**flawed_narrative())
    review["minimum_standard"][1]["status"] = "Flag"
    root = workspace(tmp_path, review, coverage_of(defect=8, clean=92))
    derived = prepare_publication(
        root, [{"id": "F1", "section": "consistency", "status": "supported"}]
    )
    assert derived["verdict"] == "Flawed"
    assert derived["findings"] == [{"id": "F1", "section": "consistency", "status": "supported"}]
    md = (root / "work/report/epoch_review.md").read_text()
    assert "Verdict: Flawed" in md and ERRORS_LEAD in md
    assert (
        "| Puzzle 86 | False negative | Prefix kept in the extracted answer. | 1 run (GPT-5.4 high) |"
        in md
    )
    assert "## Interpretation" not in md and "## Limitations" not in md


def test_publication_fills_the_date_and_refuses_the_wrong_narrative(tmp_path: Path) -> None:
    root = workspace(tmp_path, filled(review_date=""))
    prepare_publication(root, [])
    assert json.loads((root / "work/report/review.json").read_text())["review_date"]
    root = workspace(tmp_path / "b", filled(interpretation=""))
    with pytest.raises(ValueError, match="missing or empty: interpretation"):
        prepare_publication(root, [])


def test_ordinal_dates() -> None:
    assert ordinal_date("2026-10-06") == "6th October 2026"
    assert ordinal_date("2026-10-01") == "1st October 2026"
    assert ordinal_date("2026-10-22") == "22nd October 2026"
    assert ordinal_date("2026-10-13") == "13th October 2026"


def test_publication_refuses_a_verdict_that_disagrees_with_the_numbers(tmp_path: Path) -> None:
    root = workspace(tmp_path, filled(), coverage_of(defect=10, clean=10))
    with pytest.raises(ValueError, match="scoring is Pass"):
        prepare_publication(root, [])


def test_the_gl_format_is_untouched_by_default(tmp_path: Path) -> None:
    from inspect_audit._report import GL_SECTIONS, allowed_sections, report_format

    report = tmp_path / "work/report"
    report.mkdir(parents=True)
    assert report_format(report) == "gl"
    assert allowed_sections(report) == GL_SECTIONS
    (report / "format.json").write_text(json.dumps({"format": "epoch"}))
    assert allowed_sections(report) == SECTIONS
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
    assert json.loads((report / "format.json").read_text()) == {"format": "epoch"}
    assert (report / "methodology.md").is_file() and (report / "review.json").is_file()
    assert not (report / "Findings.tex").exists()
    seed = json.loads((root / "inputs/seed.json").read_text())
    assert seed["report"] == {"format": "epoch", "writing_skill": "writing-epoch"}
    prompt = prompts.investigate_prompt("epoch")
    assert "review.json" in prompt and "Findings.tex" not in prompt
    assert "Findings.tex" in prompts.investigate_prompt("gl")
    for name in WRITING_SKILLS.values():
        assert (ASSETS / "skills" / name / "SKILL.md").is_file()
    with pytest.raises(ValueError, match="cannot resume as 'gl'"):
        investigate(str(repo), resume=str(root), execution="local", report="gl")
    with pytest.raises(ValueError, match="report must be one of"):
        investigate(str(repo), output_dir=str(tmp_path / "runs2"), execution="local", report="html")


def test_methodology_names_every_row_the_code_knows() -> None:
    text = (Path(_epoch.__file__).parent / "investigation/report-epoch/methodology.md").read_text()
    for name in MINIMUM_STANDARD.values():
        assert name in text, name
    for _, statuses in QUALITY.values():
        for status in statuses:
            assert status in text, status
    for heading in (
        "Summary",
        "Methodology",
        "Interpretation",
        "Task Analysis",
        "Limitations",
        "Representative Errors",
    ):
        assert heading in text, heading
