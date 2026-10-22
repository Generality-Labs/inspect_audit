"""Actual GL template compilation and structural provenance from a frozen fixture."""

import asyncio
import hashlib
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
    findings.investigator.findings[0].criterion = ""
    with pytest.raises(ValueError, match="criterion"):
        latex.validate(findings)
    findings.investigator.findings.clear()
    findings.investigator.ratings.pop("overall")
    with pytest.raises(ValueError, match="A-D"):
        latex.validate(findings)
    with pytest.raises(ValidationError):
        latex.Prose.model_validate({"grade": "A"})
    assert load_publisher("latex").validate is latex.validate
    assert load_publisher("gl").build is not latex.build


def test_cached_template_is_verified_without_network(tmp_path: Path, monkeypatch) -> None:
    data = b"pinned template test"
    pin = tmp_path / "pin.json"
    pin.write_text(
        json.dumps(
            {
                "repository": "test/repo",
                "revision": "abc",
                "files": {"template/test.cls": hashlib.sha256(data).hexdigest()},
            }
        )
    )
    cached = tmp_path / "abc/template/test.cls"
    cached.parent.mkdir(parents=True)
    cached.write_bytes(data)
    monkeypatch.setenv("INSPECT_AUDIT_TEMPLATE_CACHE", str(tmp_path))
    monkeypatch.setattr(_template, "PIN", pin)
    assert _template.template() == tmp_path / "abc"
    cached.write_text("changed")
    with pytest.raises(ValueError, match="checksum"):
        _template.template()


@pytest.fixture
def upstream() -> Path:
    pin = json.loads(_template.PIN.read_text())
    cache = (
        Path(
            os.environ.get(
                "INSPECT_AUDIT_TEMPLATE_CACHE", Path.home() / ".cache/inspect-audit/templates"
            )
        )
        / pin["revision"]
    )
    if not cache.is_dir() and not (os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")):
        pytest.skip("private template needs GH_TOKEN or a verified cache")
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
    for finding in findings.investigator.findings:
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
    draft = _findings.Findings(
        investigator=_findings.InvestigatorFindings(summary="Scope established")
    )
    store_as(RegisterStore).data = Register().model_dump(mode="json")

    class Box:
        async def read_file(self, path: str) -> str:
            return draft.model_dump_json()

    monkeypatch.setattr(_findings, "sandbox", Box)
    with pytest.raises(ToolError, match=r"ratings\.overall"):
        asyncio.run(_findings.submit_findings(tmp_path, validate=latex.validate)())
    assert store_as(_findings.FindingsState).phase == "investigating"
    assert not (tmp_path / "submitted").exists()


@pytest.mark.docker
@pytest.mark.latex
def test_baked_cache_runs_without_network_or_github_credentials(
    tmp_path: Path, upstream: Path
) -> None:
    """The Python publishing host can use a baked cache with no runtime secret."""
    root = Path(__file__).parents[1]
    cache = tmp_path / "cache"
    shutil.copytree(upstream, cache / upstream.name)
    image = "inspect-audit:template-cache-test"
    subprocess.run(
        [
            "docker",
            "build",
            "--build-arg",
            "BASE_IMAGE=python:3.13-slim",
            "--build-context",
            f"template-cache={cache}",
            "-f",
            str(root / "src/inspect_audit/containers/template-cache.Dockerfile"),
            "-t",
            image,
            str(tmp_path),
        ],
        check=True,
        capture_output=True,
    )
    try:
        subprocess.run(
            [
                "docker",
                "run",
                "--rm",
                "--network=none",
                "-v",
                f"{root / 'src/inspect_audit/publishers/_template.py'}:/template_reader.py:ro",
                "-v",
                f"{_template.PIN}:/template.json:ro",
                "-v",
                f"{root / 'src/inspect_audit/investigation/framework.json'}:/framework.json:ro",
                image,
                "python",
                "-c",
                "import json, os, runpy; from pathlib import Path; "
                "assert not os.environ.get('GH_TOKEN') and not os.environ.get('GITHUB_TOKEN'); "
                "reader = runpy.run_path('/template_reader.py'); "
                "assert json.loads(Path('/framework.json').read_text()) == reader['regenerate'](reader['template']())",
            ],
            check=True,
            capture_output=True,
        )
    finally:
        subprocess.run(["docker", "image", "rm", image], check=True, capture_output=True)
