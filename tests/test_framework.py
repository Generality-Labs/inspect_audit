"""The framework provides reference definitions; findings owns all judgments."""

import json
from pathlib import Path

from inspect_audit._framework import framework_definitions, stage_framework


def test_framework_definitions_and_provenance(tmp_path: Path) -> None:
    stage_framework(tmp_path)
    definitions = framework_definitions(tmp_path)
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
