"""Actual GL template compilation and structural provenance from a frozen fixture."""

import asyncio
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest
from pydantic import ValidationError

from inspect_audit._findings import read_bundle
from inspect_audit._publishing import load_publisher
from inspect_audit.publishers import _template, latex

FIXTURE = Path(__file__).parent / "fixtures/findings_bundle"


def test_latex_requires_recorded_grade_and_criterion() -> None:
    findings, _ = read_bundle(FIXTURE)
    latex.validate(findings)
    findings.auditor.findings[0].criterion = ""
    with pytest.raises(ValueError, match="criterion"):
        latex.validate(findings)
    findings.auditor.findings.clear()
    findings.auditor.ratings.pop("overall")
    with pytest.raises(ValueError, match="A-D"):
        latex.validate(findings)
    with pytest.raises(ValidationError):
        latex.Prose.model_validate({"grade": "A"})
    assert load_publisher("latex").validate is latex.validate
    assert load_publisher("gl").build is not latex.build


def test_the_template_ships_every_file_the_publisher_needs() -> None:
    """No download and no token: the pinned files are part of the package."""
    pin = json.loads(_template.PIN.read_text())
    assert pin["repository"] == "Generality-Labs/audit_reports" and len(pin["revision"]) == 40
    missing = [n for n in pin["files"] if not (_template.template() / n).is_file()]
    assert missing == []


@pytest.fixture
def upstream() -> Path:
    return _template.template()


@pytest.mark.latex
def test_registry_is_regenerated_from_the_pinned_template(upstream: Path) -> None:
    from inspect_audit._framework import REGISTRY

    assert json.loads(REGISTRY.read_text()) == _template.regenerate(upstream)


@pytest.mark.latex
@pytest.mark.publishing
def test_fixture_pdf_uses_bundle_scorecard_and_finding_boxes(
    tmp_path: Path, monkeypatch, upstream: Path
) -> None:
    if not shutil.which("latexmk") and not os.environ.get("INSPECT_AUDIT_LATEX_IMAGE"):
        pytest.skip("needs host latexmk or INSPECT_AUDIT_LATEX_IMAGE")
    pin = json.loads(_template.PIN.read_text())

    class Box:
        async def read_file(self, path: str) -> str:
            assert path == "/workspace/report/prose.json"
            return json.dumps(
                {
                    "datasheet": "Synthetic fixture benchmark.",
                    "setup": "No model calls were made.",
                    "dimensions": {"C": "The reference was checked directly."},
                }
            )

    monkeypatch.setattr(latex, "sandbox", Box)
    output = asyncio.run(latex.build(tmp_path, FIXTURE))
    text = subprocess.check_output(
        ["pdftotext", "-layout", str(output / "report.pdf"), "-"], text=True
    )
    generated = (output / "Content.tex").read_text()
    findings, _ = read_bundle(FIXTURE)
    for title in (
        "Cover Sheet",
        "Contents",
        "Eval Scorecard",
        "Eval Datasheet",
        "Audit Setup",
        "Audit Findings",
        "Scoring Criteria",
    ):
        assert title.lower() in text.lower()
    assert r"\evalscore{C}" in (output / "report.tex").read_text()
    assert generated.count(r"\setdimension{") == 9
    assert r"\setdimension{dataset}{\assessMajor}" in generated
    assert r"\setdimension{harness}{\assessNotAssessed}" in generated
    assert r"\contributionrow{C.1}{incorrect reference answer}{\assessMajor}" in generated
    assert r"\begin{finding}[C.1]" in generated
    assert "Finding C.1" in text and "Finding F-1" in text
    assert "1/3 selected samples" in text
    for finding in findings.auditor.findings:
        assert finding.issue in text
        assert finding.evidence[0].source in text
    assert (output / "report.pdf").read_bytes().startswith(b"%PDF")
    assert json.loads((output / "template-provenance.json").read_text()) == pin
    shutil.copyfile(output / "report.pdf", tmp_path / "fixture.pdf")


def test_invalid_report_grade_is_refused_before_findings_are_frozen(
    tmp_path: Path, monkeypatch
) -> None:
    from inspect_ai.tool import ToolError
    from inspect_ai.util import Store, store_as
    from inspect_ai.util._store import init_subtask_store

    from inspect_audit import _findings
    from inspect_audit._register import Register, RegisterStore

    init_subtask_store(Store())
    draft = _findings.Findings(auditor=_findings.AuditorFindings(summary="Scope established"))
    store_as(RegisterStore).data = Register().model_dump(mode="json")

    class Box:
        async def read_file(self, path: str) -> str:
            return draft.model_dump_json()

    monkeypatch.setattr(_findings, "sandbox", Box)
    with pytest.raises(ToolError, match=r"ratings\.overall"):
        asyncio.run(_findings.submit_findings(tmp_path, validate=latex.validate)())
    assert store_as(_findings.FindingsState).phase == "auditing"
    assert not (tmp_path / "submitted").exists()


def test_source_preserves_bracketed_findings_and_benchmark_wide_scope(tmp_path: Path) -> None:
    (tmp_path / "reports/Example").mkdir(parents=True)
    (tmp_path / "reports/Example/Content.tex").write_text(
        r"\auditpart{Contents} contents \auditpart{Eval Scorecard} [Headline assessment: example] [Capability/Safety -- domain] \auditpart{Eval Datasheet}"
    )
    (tmp_path / "reports/Example/Example_Audit.tex").write_text(r"\evalscore{A}")
    findings, manifest = read_bundle(FIXTURE)
    findings.auditor.ratings["overall"].explanation = "Issue [sample 12]"
    findings.auditor.ratings["overall"].evidence[0].source = "reference [v2]"
    findings.subject["domain"] = "science [physics]"
    findings.auditor.findings[0].sample_ids = []
    _, content = latex.source(findings, manifest, latex.Prose(), tmp_path)
    assert "Issue [sample 12]" in content
    assert "reference [v2]" in content
    assert "science [physics]" in content
    assert "Benchmark-wide finding; no individual sample count claimed." in content
