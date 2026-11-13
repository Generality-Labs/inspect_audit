"""The framework provides reference definitions; findings owns all judgments."""

import json
from pathlib import Path

from inspect_audit._framework import stage_framework


def test_framework_definitions_and_provenance(tmp_path: Path) -> None:
    stage_framework(tmp_path)
    definitions = json.loads((tmp_path / "framework/checks.json").read_text())
    assert len([key for key in definitions if "." not in key]) == 9
    assert definitions["D.2"]["name"] == "planning or agent loop"
    assert definitions["G.4"]["name"] == "surface-form sensitivity"
    assert "models differently" in definitions["H.3"]["definition"]
    assert "model scores cluster" in definitions["I.3"]["definition"]
    framework = tmp_path / "framework"
    assert json.loads((framework / "checks.json").read_text()) == definitions
    provenance = json.loads((framework / "provenance.json").read_text())
    from inspect_audit.publishers._template import PIN

    assert provenance == json.loads(PIN.read_text())
    assert definitions["A.0"]["name"] == "measurement distortion"
    criteria = json.loads((framework / "criteria.json").read_text())
    assert set(criteria["grades"]) == set("ABCD")
    assert not list(framework.rglob("*.tex"))
    assert not list(framework.rglob("*.sty"))
    assert sorted(p.name for p in tmp_path.iterdir()) == ["framework"]


def test_one_framework_source_the_skill_and_publisher_follow() -> None:
    """framework.json is the one source: the skill's definitions are generated from it, and
    the publisher's assessment macros cover exactly its assessments (round 8 rated
    "F.3=D" when the scales were stated in several places, FRICTION 104)."""
    import json

    from inspect_audit import _framework
    from inspect_audit.publishers.latex import ASSESSMENTS

    registry = json.loads(_framework.REGISTRY.read_text())
    text = _framework.SKILL.read_text()
    generated = text.split(_framework.BEGIN, 1)[1].split(_framework.END, 1)[0].strip()
    assert generated == _framework.skill_definitions(registry), (
        "regenerate: python -m inspect_audit.publishers._template --update-registry"
    )
    assert set(ASSESSMENTS) == set(registry["assessments"])


def test_a_letter_grade_on_a_criterion_is_refused_with_the_rule() -> None:
    import pytest

    from inspect_audit._findings import AuditorFindings, Findings, Rating
    from inspect_audit._verdicts import Evidence
    from inspect_audit.publishers.latex import validate

    evidence = [Evidence(observed="o", source="s")]
    rating = lambda label: Rating(label=label, explanation="e", evidence=evidence)  # noqa: E731
    findings = Findings(
        auditor=AuditorFindings(summary="s", ratings={"overall": rating("B"), "F.3": rating("D")})
    )
    with pytest.raises(
        ValueError, match=r"F\.3=D is not an assessment: ratings\.overall takes a grade"
    ):
        validate(findings)
