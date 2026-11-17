"""The Epoch package, installed as its own distribution beside inspect_audit."""

import asyncio
import hashlib
import json
from collections.abc import Awaitable
from pathlib import Path

import pytest
from inspect_audit_epoch import rubric

from inspect_audit.package import (
    Evidence,
    Finding,
    Findings,
    Rating,
    encoded,
    load_package,
    select,
)

EVIDENCE = [Evidence(observed="o", source="s")]


async def _built(build: Awaitable[Path]) -> Path:
    return await build


def rated(**labels: str) -> dict[str, Rating]:
    """Every row Not Reviewed, except those given."""
    ratings = {r["id"]: "Not Reviewed" for r in rubric.rows()} | labels
    return {
        k: Rating(label=v, explanation=f"{k}: 31 of 200 graded wrongly", evidence=EVIDENCE)
        for k, v in ratings.items()
    }


def findings(ratings: dict[str, Rating], *, criterion: str = "scoring_integrity") -> Findings:
    return Findings.model_validate(
        {
            "auditor": {
                "summary": "s",
                "ratings": {k: v.model_dump() for k, v in ratings.items()},
                "findings": [
                    Finding(
                        criterion=criterion, issue="i", explanation="e", evidence=EVIDENCE
                    ).model_dump()
                ],
            }
        }
    )


PASSING = {
    "reviewability": "Full",
    "scoring_integrity": "Pass",
    "benchmark_consistency": "Pass",
    "elicitation_adequacy": "Pass",
    "evaluation_bias": "Pass",
}


def test_it_is_found_by_name_through_its_entry_point() -> None:
    chosen = select("epoch", None)
    assert (chosen.name, chosen.format) == ("epoch", "page")
    assert chosen.package.rubric.skill.name == "epoch-rubric"


@pytest.mark.parametrize(
    ("labels", "expected"),
    [
        ({"reviewability": "Inadequate"}, "NEI"),
        ({}, "Incomplete"),
        (PASSING, "Verified"),
        ({**PASSING, "benchmark_consistency": "Not Reviewed"}, "Incomplete"),
        (
            {
                "reviewability": "Partial",
                "scoring_integrity": "Pass",
                "benchmark_consistency": "Flag",
            },
            "Flawed",
        ),
    ],
)
def test_the_verdict_follows_from_the_rows(labels: dict[str, str], expected: str) -> None:
    result, reasons = rubric.verdict(rated(**labels))
    assert result == expected and reasons
    rubric.validate(findings(rated(**labels)))


def test_quality_rows_never_change_the_verdict() -> None:
    labels = {**PASSING, "construct_validity": "Does not measure stated capabilities"}
    assert rubric.verdict(rated(**labels))[0] == "Verified"


def test_the_review_stops_at_the_first_flag() -> None:
    labels = {
        "reviewability": "Full",
        "scoring_integrity": "Flag",
        "benchmark_consistency": "Pass",
    }
    with pytest.raises(ValueError, match="benchmark_consistency must be Not Reviewed"):
        rubric.validate(findings(rated(**labels)))


def test_inadequate_reviewability_leaves_everything_else_unreviewed() -> None:
    with pytest.raises(ValueError, match="scoring_integrity must be Not Reviewed"):
        rubric.validate(findings(rated(reviewability="Inadequate", scoring_integrity="Pass")))


def test_a_flag_states_its_measured_impact() -> None:
    ratings = rated(reviewability="Full", scoring_integrity="Pass", benchmark_consistency="Flag")
    ratings["benchmark_consistency"] = Rating(
        label="Flag", explanation="the scorer changed silently", evidence=EVIDENCE
    )
    with pytest.raises(ValueError, match="measured impact"):
        rubric.validate(findings(ratings))


@pytest.mark.parametrize(
    ("ratings", "match"),
    [
        ({"overall": "A"}, "Unknown Epoch rows overall"),
        ({"reviewability": "Pass"}, "not a status of this row: Full, Partial"),
    ],
)
def test_ratings_are_the_forms_rows_and_statuses(ratings: dict[str, str], match: str) -> None:
    bad = rated() | {
        k: Rating(label=v, explanation="e", evidence=EVIDENCE) for k, v in ratings.items()
    }
    with pytest.raises(ValueError, match=match):
        rubric.validate(findings(bad))


def test_every_row_is_rated_and_every_finding_names_its_row() -> None:
    partial = rated()
    partial.pop("contamination")
    with pytest.raises(ValueError, match="missing contamination"):
        rubric.validate(findings(partial))
    with pytest.raises(ValueError, match="names its Epoch row"):
        rubric.validate(findings(rated(), criterion="C.1"))


def test_the_skills_are_generated_and_load_as_inspect_reads_them() -> None:
    from inspect_ai.tool import read_skills

    assert rubric.SKILL.read_text() == rubric.skill_text(), (
        "regenerate: python -m inspect_audit_epoch.rubric"
    )
    _, publisher = load_package("epoch").publisher()
    loaded = read_skills([str(rubric.SKILL.parent), *publisher.skills])
    assert [s.name for s in loaded] == ["epoch-rubric", "epoch-page"]


def test_the_stage_is_the_form(tmp_path: Path) -> None:
    rubric.stage(tmp_path)
    staged = json.loads((tmp_path / "rubric.json").read_text())
    assert [r["id"] for r in staged["rows"]] == [r["id"] for r in rubric.rows()]


def test_the_page_prints_the_derived_verdict(tmp_path: Path) -> None:
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    submitted = findings(rated(**PASSING))
    content = encoded(submitted.model_dump(mode="json"))
    (bundle / "findings.json").write_bytes(content)
    record = {"sha256": hashlib.sha256(content).hexdigest(), "bytes": len(content)}
    (bundle / "manifest.json").write_bytes(encoded({"files": {"findings.json": record}}))
    _, publisher = load_package("epoch").publisher()
    out = asyncio.run(_built(publisher.build(tmp_path, bundle)))
    assert (out / "review.md").read_text().startswith("# Verified")
    assert json.loads((out / "verdict.json").read_text())["verdict"] == "Verified"
