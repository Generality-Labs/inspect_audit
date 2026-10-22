"""Taxonomies are versioned data, not code; findings name the one they use."""

from pathlib import Path

import pytest
from pydantic import ValidationError

import inspect_audit
from inspect_audit._assessment import framework_definitions
from inspect_audit.findings.models import CodeLocation, Finding, Source
from inspect_audit.findings.taxonomy import (
    TAXONOMIES,
    load_mapping,
    load_taxonomy,
    map_check,
    map_dimension,
)


def test_v1_matches_the_pinned_latex_framework() -> None:
    report = Path(inspect_audit.__file__).parent / "investigation" / "report"
    defs = framework_definitions(report)
    tax = load_taxonomy("gl-audit@1")
    assert tax.status == "pinned"
    assert {d.id for d in tax.dimensions} == {v["dimension"] for v in defs.values()}
    assert tax.check_ids() == {k for k in defs if "." in k}
    for dim in tax.dimensions:
        assert dim.code is not None and defs[dim.code]["dimension"] == dim.id
        for contribution in dim.contributions:
            assert contribution.name == defs[contribution.id]["name"]


def test_v2_is_the_strategy_taxonomy_and_still_a_draft() -> None:
    tax = load_taxonomy("gl-audit@2")
    assert tax.status == "draft"
    assert [d.id for d in tax.dimensions] == [
        "implementation",
        "sensitivity",
        "informativeness",
        "comparability",
        "practical_reproducibility",
        "construct_validity",
        "content_validity",
    ]
    implementation = next(d for d in tax.dimensions if d.id == "implementation")
    assert [c.id.split(".")[1] for c in implementation.contributions] == [
        "task_specification",
        "environment",
        "scaffolding",
        "harness",
        "grading",
    ]
    assert "incorrect_reference_answer" in implementation.contributions[0].items


def test_every_v1_dimension_and_check_maps_to_v2() -> None:
    v1, v2 = load_taxonomy("gl-audit@1"), load_taxonomy("gl-audit@2")
    mapping = load_mapping("gl-audit@1", "gl-audit@2")
    assert set(mapping.dimensions) == v1.dimension_ids()
    assert set(mapping.dimensions.values()) <= v2.dimension_ids()
    assert set(mapping.checks) == v1.check_ids()
    assert set(mapping.checks.values()) <= v2.check_ids()
    assert map_dimension("dataset", "gl-audit@1", "gl-audit@2") == "implementation"
    assert map_check("C.1", "gl-audit@1", "gl-audit@2") == "implementation.task_specification"


def test_unknown_taxonomy_ref_is_an_error() -> None:
    with pytest.raises(ValueError, match="unknown taxonomy"):
        load_taxonomy("gl-audit@9")
    assert set(TAXONOMIES) == {"gl-audit@1", "gl-audit@2"}


def _finding(**overrides: object) -> Finding:
    data: dict[str, object] = {
        "fingerprint": "sha256:0",
        "producer": "p",
        "rule": "r",
        "subject": {"eval": "inspect_evals/x", "revision": {"commit": "abc"}},
        "dimension": "dataset",
        "severity": "minor",
        "status": "supported",
        "summary": "s",
        "locations": [CodeLocation(role="primary", file="f.py")],
        "run_id": "run",
        "source": Source(format="f", record=None),
    }
    data.update(overrides)
    return Finding.model_validate(data)


def test_finding_defaults_to_v1_and_validates_dimension_against_it() -> None:
    assert _finding().taxonomy == "gl-audit@1"
    with pytest.raises(ValidationError, match="not a dimension of gl-audit@1"):
        _finding(dimension="implementation")
    with pytest.raises(ValidationError, match="not a check of gl-audit@1"):
        _finding(check="Z.9")
    assert _finding(check="C.1").check == "C.1"


def test_finding_can_use_v2_ids() -> None:
    finding = _finding(
        taxonomy="gl-audit@2", dimension="implementation", check="implementation.grading"
    )
    assert finding.check == "implementation.grading"
    with pytest.raises(ValidationError, match="not a dimension of gl-audit@2"):
        _finding(taxonomy="gl-audit@2", dimension="dataset")
