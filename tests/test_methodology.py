"""A methodology is data the loader validates; the package copy is the one in the docs."""

from pathlib import Path

import pytest

from inspect_audit.methodology import (
    METHODOLOGIES,
    Threshold,
    available_methodologies,
    load_methodology,
)


def test_the_epoch_rubric_loads_with_its_structure() -> None:
    m = load_methodology("epoch-rubric@1")
    assert m.key == "epoch-rubric@1" and m.status == "draft"
    assert m.gate.id == "reviewability" and m.gate.scale[:3] == ["Full", "Partial", "Inadequate"]
    assert [c.id for c in m.minimum] == [
        "scoring_integrity",
        "benchmark_consistency",
        "elicitation_adequacy",
        "evaluation_bias",
    ]
    assert len(m.quality) == 7 and m.quality[0].id == "resource_adequacy"
    # scoring is decided by the item audits alone; the benchmark-wide checks flag on a measured effect
    assert [c.flag_requires_impact for c in m.minimum] == [False, True, True, True]
    assert all(c.scale == ["Pass", "Flag", "Not Reviewed"] for c in m.minimum)
    scoring = m.check("scoring_integrity")
    assert scoring.threshold is not None and scoring.threshold.bound == 0.2
    assert scoring.threshold.measure == m.sampling.measure.name == "sampled_defect_rate"
    assert m.check("contamination").fields == ["as_of", "tasks_public_pct", "solutions_public_pct"]
    assert m.sections == [
        "1. Reviewability",
        "2. Scoring (minimum standard)",
        "3. Evaluation quality",
    ]
    assert "{creator}" in m.intro["disclaimer"]
    assert m.verdict.procedure.otherwise == "Verified"
    assert available_methodologies() == ["epoch-rubric@1"]
    with pytest.raises(KeyError):
        m.check("vibes")
    with pytest.raises(FileNotFoundError, match="available: epoch-rubric-1"):
        load_methodology("gl-audit@9")


def test_thresholds_compare_as_written() -> None:
    t = Threshold(measure="sampled_defect_rate", flag_when=">= 0.20")
    assert t.exceeded(0.2) and t.exceeded(0.5) and not t.exceeded(0.19)
    assert Threshold(measure="x", flag_when="< 5").exceeded(4)
    with pytest.raises(ValueError, match="cannot read threshold"):
        Threshold(measure="x", flag_when="lots").exceeded(1)


def test_the_docs_copy_matches_the_package_copy() -> None:
    root = Path(__file__).resolve().parents[1]
    docs = root / "docs/methodologies/epoch-rubric-1.yaml"
    assert docs.read_text() == (METHODOLOGIES / "epoch-rubric-1.yaml").read_text()
