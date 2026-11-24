"""Actual GL template compilation and structural provenance from a frozen fixture."""

import asyncio
import json
import os
import re
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
    with pytest.raises(ValueError, match=r"overall takes a grade \(A, B, C, D\)"):
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
                    # typographic punctuation is set; a progress bar's block, an
                    # emoji and CJK become "?" rather than stop the report (docvqa)
                    "datasheet": "Synthetic fixture benchmark \u2013 \u201cquoted\u201d\u2026 "
                    "caf\u00e9 \u2588\u258e \U0001f600 \u6f22.",
                    "setup": "No model calls were made.",
                    "dimensions": {"C": "The reference was checked directly.\n\n{{excerpt:ref}}"},
                    "excerpts": [
                        {
                            "id": "ref",
                            "source": "/workspace/reference.txt",
                            "caption": "The cited reference",
                            "redact": ["9"],
                        }
                    ],
                }
            )

    monkeypatch.setattr(latex, "sandbox", Box)
    output = asyncio.run(latex.build(tmp_path, FIXTURE))
    text = subprocess.check_output(
        ["pdftotext", "-layout", str(output / "report.pdf"), "-"], text=True
    )
    generated = (output / "Content.tex").read_text()
    findings, _ = read_bundle(FIXTURE)
    # the datasheet with all of them compiled (pdftotext splits accented letters, so the
    # check is its plain words), and nothing unsettable reached the source
    assert "quoted" in text and "\u2588" not in generated and "\U0001f600" not in generated
    # the excerpt as recorded, with the string it names redacted; where it was read
    # from is kept beside the report, not printed in it
    assert "The correct reference value is [redacted]." in text
    assert "/workspace/reference.txt" in (output / "excerpts.txt").read_text()
    assert "/workspace/reference.txt" not in text and "Appendix" not in text
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
        # the evidence lists stay in the published findings; the PDF is read in one sitting
        assert finding.evidence[0].observed not in text
        assert finding.evidence[0].source not in text
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
    from inspect_audit._register import Register, save

    init_subtask_store(Store())
    draft = _findings.Findings(auditor=_findings.AuditorFindings(summary="Scope established"))
    save(Register())

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
    findings.auditor.findings[0].explanation = "reference [v2]"
    findings.subject["domain"] = "science [physics]"
    findings.auditor.findings[0].sample_ids = []
    _, content = latex.source(findings, manifest, latex.Prose(), tmp_path)
    assert "Issue [sample 12]" in content
    assert "reference [v2]" in content
    assert "science [physics]" in content
    assert "Benchmark-wide finding; no individual sample count claimed." in content


def test_prose_under_a_sub_criterion_is_refused_by_name_as_a_tool_error(tmp_path: Path) -> None:
    """Breadth round, gsm8k: the agent keyed prose under "C.1"; the preview tool let the
    ValueError end the audit. Now it names the keys, and previews report it as a tool error."""
    from inspect_ai.tool import ToolError

    from inspect_audit.publishers._render import reported

    (tmp_path / "reports/Example").mkdir(parents=True)
    (tmp_path / "reports/Example/Content.tex").write_text(
        r"\auditpart{Contents} x \auditpart{Eval Scorecard} x \auditpart{Eval Datasheet}"
    )
    (tmp_path / "reports/Example/Example_Audit.tex").write_text(r"\evalscore{A}")
    findings, manifest = read_bundle(FIXTURE)
    prose = latex.Prose(dimensions={"C": "fine", "C.1": "under a sub-criterion"})
    with pytest.raises(ToolError, match=r"Preview failed: .*not C\.1"), reported("Preview"):
        latex.source(findings, manifest, prose, tmp_path)


def test_characters_pdflatex_cannot_set_become_a_question_mark() -> None:
    assert latex.tex("caf\u00e9 \u2013 \u2588 \U0001f600") == "caf\u00e9 \u2013 ? ?"


def test_unbreakable_text_may_break_rather_than_run_off_the_page() -> None:
    from inspect_audit.publishers.latex import tex

    assert (
        tex("inspect_evals/swe_bench")
        == r"inspect\_\allowbreak{}evals/\allowbreak{}swe\_\allowbreak{}bench"
    )
    digest = "3c0882717ba75ae0b0ac68d3b55e515d19766b90bd5ec50b16e0158605fd1fc"
    assert tex(digest).count(r"\allowbreak{}") == len(digest) - 1
    assert tex("plain words stay as they are") == "plain words stay as they are"


async def _no_box(probe: str) -> bool:
    return False


async def _box_with_tex(probe: str) -> bool:
    return True


def _image_check(returncode: int, calls: list[list[str]]):
    async def run(command: list[str], **kwargs: object) -> object:
        calls.append(command)
        return type("Result", (), {"success": returncode == 0})()

    return run


def test_an_audit_with_nothing_to_compile_its_report_stops_before_it_starts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("INSPECT_AUDIT_LATEX_IMAGE", raising=False)
    monkeypatch.setattr(latex, "box_has", _no_box)
    monkeypatch.setattr(shutil, "which", lambda name: None)
    with pytest.raises(RuntimeError, match="Nothing can compile the report"):
        asyncio.run(latex.preflight())


def test_tex_in_the_auditors_box_is_enough(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("INSPECT_AUDIT_LATEX_IMAGE", raising=False)
    monkeypatch.setattr(latex, "box_has", _box_with_tex)
    monkeypatch.setattr(shutil, "which", lambda name: None)
    asyncio.run(latex.preflight())


def test_tex_on_the_host_is_enough(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("INSPECT_AUDIT_LATEX_IMAGE", raising=False)
    monkeypatch.setattr(latex, "box_has", _no_box)
    monkeypatch.setattr(shutil, "which", lambda name: f"/usr/bin/{name}")
    asyncio.run(latex.preflight())


def test_a_named_latex_image_must_exist(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("INSPECT_AUDIT_LATEX_IMAGE", "inspect-audit:publishing")
    monkeypatch.setattr(latex, "box_has", _no_box)
    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/docker")
    calls: list[list[str]] = []
    monkeypatch.setattr(latex, "subprocess", _image_check(0, calls))
    asyncio.run(latex.preflight())
    assert calls == [["docker", "image", "inspect", "inspect-audit:publishing"]]
    monkeypatch.setattr(latex, "subprocess", _image_check(1, calls))
    with pytest.raises(RuntimeError, match="inspect-audit:publishing"):
        asyncio.run(latex.preflight())


# ---- Excerpts: verbatim evidence the publisher reads itself -----------------


def _example(tmp_path: Path) -> Path:
    """The pinned Example reduced to what `source` cuts from it."""
    (tmp_path / "reports/Example").mkdir(parents=True)
    (tmp_path / "reports/Example/Content.tex").write_text(
        r"\auditpart{Contents} x \auditpart{Eval Scorecard} x \auditpart{Eval Datasheet}"
    )
    (tmp_path / "reports/Example/Example_Audit.tex").write_text(r"\evalscore{A}")
    return tmp_path


def _transcript(path: Path) -> str:
    """A finished log, written by Inspect's own writer: one attempt with a tool call."""
    from inspect_ai.log import (
        EvalConfig,
        EvalDataset,
        EvalLog,
        EvalSample,
        EvalSpec,
        write_eval_log,
    )
    from inspect_ai.model import (
        ChatMessageAssistant,
        ChatMessageTool,
        ChatMessageUser,
        ModelOutput,
    )
    from inspect_ai.tool import ToolCall

    write_eval_log(
        EvalLog(
            eval=EvalSpec(
                created="2026-01-01T00:00:00+00:00",
                task="fixture",
                dataset=EvalDataset(),
                model="mockllm/model",
                config=EvalConfig(),
            ),
            status="success",
            samples=[
                EvalSample(
                    id="item-1",
                    epoch=2,
                    input="What is six plus seven?",
                    target="thirteen",
                    messages=[
                        ChatMessageUser(content="What is six plus seven?"),
                        ChatMessageAssistant(
                            id="call-msg",
                            content="I will look it up.",
                            tool_calls=[
                                ToolCall(
                                    id="t1",
                                    function="bash",
                                    arguments={"cmd": "grep -n 'six' answers.txt\ncat key.txt"},
                                )
                            ],
                        ),
                        ChatMessageTool(
                            id="result-msg",
                            tool_call_id="t1",
                            content="3:six plus seven = thirteen\nkey: thirteen",
                        ),
                    ],
                    output=ModelOutput.from_content("mockllm/model", "thirteen"),
                )
            ],
        ),
        str(path),
    )
    return str(path)


def _with_log(manifest: dict, source: str, address: str) -> dict:
    return {
        **manifest,
        "log_references": [{"source": source, "address": address, "sha256": "x", "bytes": 1}],
    }


def test_a_file_excerpt_is_read_from_the_bundle_and_quoted_in_place(tmp_path: Path) -> None:
    from inspect_audit.publishers._excerpt import Excerpt, resolve_all

    findings, manifest = read_bundle(FIXTURE)
    prose = latex.Prose(
        dimensions={"C": "The reference disagrees.\n\n{{excerpt:ref}}\n\nIt says 9, not 7."},
        excerpts=[
            Excerpt(
                id="ref",
                source="/workspace/reference.txt",
                lines=(1, 1),
                caption="The cited reference",
                redact=["9"],
            )
        ],
    )
    quotes = resolve_all(prose.excerpts, FIXTURE, manifest)
    assert quotes["ref"].lines == ["The correct reference value is [redacted]."]
    assert quotes["ref"].reference == "/workspace/reference.txt, lines 1-1"
    # a .txt file is prose: a block quotation in the text font, not a code box
    assert quotes["ref"].kind == "text" and quotes["ref"].link is None
    _, content = latex.source(findings, manifest, prose, _example(tmp_path), quotes)
    box = content[content.index(r"\begin{iaquote}") : content.index(r"\end{iaquote}")]
    assert box == (
        r"\begin{iaquote}{The cited reference}\iaqpar{The correct reference value is [redacted].}"
    )
    # where it was read from is not printed; the text before and after is kept
    assert "reference.txt" not in content and "iasource" not in content
    assert "The reference disagrees." in content and "It says 9, not 7." in content
    # declared as code, it is set line for line
    code = resolve_all([prose.excerpts[0].model_copy(update={"kind": "code"})], FIXTURE, manifest)[
        "ref"
    ]
    assert latex.excerpt_box(code).startswith(
        r"\begin{iaexcerpt}{The cited reference}\iaxline{0}{The\ correct"
    )


def test_a_transcript_excerpt_is_read_from_its_log_as_recorded(
    tmp_path: Path,
) -> None:
    from inspect_audit.publishers._excerpt import Excerpt, resolve_all

    _, manifest = read_bundle(FIXTURE)
    log = _transcript(tmp_path / "attempt.eval")
    manifest = _with_log(manifest, "/inputs/logs/0/attempt.eval", log)
    by_id = Excerpt(
        id="lookup",
        source="/inputs/logs/0/attempt.eval",
        sample="item-1",
        epoch=2,
        message="call-msg",
        caption="The agent looks the answer up",
    )
    by_position = by_id.model_copy(
        update={"id": "result", "message": 2, "start": "key:", "redact": []}
    )
    prompt = by_id.model_copy(update={"id": "prompt", "message": 0})
    words = by_id.model_copy(update={"id": "words", "lines": (1, 1)})
    quotes = resolve_all([by_id, by_position, prompt, words], FIXTURE, manifest)
    # the call's command as written, not JSON-quoted
    assert quotes["lookup"].lines == [
        "I will look it up.",
        "bash: grep -n 'six' answers.txt",
        "cat key.txt",
    ]
    # nothing is redacted unless the declaration says so, the recorded answer included
    assert quotes["result"].lines == ["key: thirteen"]
    assert quotes["result"].redactions == 0
    assert "sample item-1, epoch 2, message 2 (tool, id result-msg)" in quotes["result"].reference
    # a tool call or its output is code; a prompt, or a model's own words, is prose
    assert [quotes[n].kind for n in ("lookup", "result", "prompt", "words")] == [
        "code",
        "code",
        "text",
        "text",
    ]
    # a log not on Hawk has no viewer page to link to
    assert all(q.link is None for q in quotes.values())
    # a log known only by its place in the audit's inputs is read from the audit's host
    staged = tmp_path / "audit/inputs/logs/0/attempt.eval"
    staged.parent.mkdir(parents=True)
    shutil.copyfile(log, staged)
    inputs_only = _with_log(manifest, "/inputs/logs/0/attempt.eval", "/inputs/logs/0/attempt.eval")
    assert resolve_all([by_id], FIXTURE, inputs_only, tmp_path / "audit")["lookup"].lines
    with pytest.raises(ValueError, match="only in the original audit's inputs"):
        resolve_all([by_id], FIXTURE, inputs_only)


def test_long_excerpts_are_cut_and_say_what_they_left_out(tmp_path: Path) -> None:
    from inspect_audit.publishers._excerpt import Excerpt, plain, resolve_all

    _, manifest = read_bundle(FIXTURE)
    log = _transcript(tmp_path / "attempt.eval")
    manifest = _with_log(manifest, "/inputs/logs/0/attempt.eval", log)
    excerpt = Excerpt(
        id="cut",
        source=log,
        sample="item-1",
        epoch=2,
        message=1,
        max_lines=1,
        caption="Cut",
    )
    [quote] = resolve_all([excerpt], FIXTURE, manifest).values()
    assert quote.lines == ["I will look it up."] and quote.elided == 2
    assert plain(quote).endswith("[... 2 more lines not shown]")
    assert r"\iaxelided{2}" in latex.excerpt_box(quote)


@pytest.mark.parametrize(
    ("change", "error"),
    [
        ({"source": "/workspace/missing.txt"}, "neither a file in the findings bundle"),
        ({"start": "reference value is 7"}, "not in the source verbatim"),
        ({"start": "The correct", "end": "value is 7"}, "not in the source verbatim after"),
        ({"lines": (1, 5)}, "run past the source's 1 lines"),
        ({"redact": ["seven"]}, "does not occur in the selected lines"),
        (
            {"source": "/inputs/logs/9/other.eval", "sample": "a", "epoch": 1, "message": 0},
            "not a log the findings reference",
        ),
    ],
)
def test_an_excerpt_that_does_not_resolve_fails_the_preview_by_name(
    change: dict, error: str
) -> None:
    from inspect_ai.tool import ToolError

    from inspect_audit.publishers._excerpt import Excerpt, resolve_all
    from inspect_audit.publishers._render import reported

    _, manifest = read_bundle(FIXTURE)
    excerpt = Excerpt.model_validate(
        {"id": "ref", "source": "/workspace/reference.txt", "caption": "C", **change}
    )
    with (
        pytest.raises(ToolError, match=f"Preview failed: excerpt ref: .*{error}"),
        reported("Preview"),
    ):
        resolve_all([excerpt], FIXTURE, manifest)


@pytest.mark.parametrize(
    ("datasheet", "error"),
    [
        ("No placement.", "ref are declared but not placed"),
        ("{{excerpt:ref}} and again {{excerpt:ref}}", "placed twice"),
        ("{{excerpt:ref}} {{excerpt:other}}", "no excerpt with that id is declared"),
    ],
)
def test_each_declared_excerpt_is_placed_exactly_once(
    tmp_path: Path, datasheet: str, error: str
) -> None:
    from inspect_audit.publishers._excerpt import Excerpt, resolve_all

    findings, manifest = read_bundle(FIXTURE)
    prose = latex.Prose(
        datasheet=datasheet,
        excerpts=[Excerpt(id="ref", source="/workspace/reference.txt", caption="C")],
    )
    quotes = resolve_all(prose.excerpts, FIXTURE, manifest)
    with pytest.raises(ValueError, match=error):
        latex.source(findings, manifest, prose, _example(tmp_path), quotes)


def test_an_excerpt_declaration_is_checked_before_it_is_read() -> None:
    from inspect_audit.publishers._excerpt import Excerpt

    with pytest.raises(ValidationError, match="names sample, epoch and message"):
        Excerpt(id="x", source="/inputs/a.eval", sample="a", caption="C")
    with pytest.raises(ValidationError, match="not both"):
        Excerpt(id="x", source="/workspace/a", lines=(1, 2), start="a", caption="C")
    with pytest.raises(ValidationError):
        Excerpt(id="x y", source="/workspace/a", caption="C")
    with pytest.raises(ValidationError):
        Excerpt(id="x", source="/workspace/a", caption="C", max_lines=500)


def test_recorded_code_keeps_its_spacing_and_cannot_set_tex() -> None:
    assert latex.code("  a {b}_c") == r"\ \ a\ \{b\}\_\allowbreak{}c"
    assert latex.code("x--y,'z'") == r"x-{}-{}y,{}\textquotesingle{}z\textquotesingle{}"
    # a run with nowhere to break may break anywhere rather than leave the box
    assert latex.code("a" * 40).count(r"\allowbreak{}") == 40
    assert latex.code(r"\end{iaexcerpt}").startswith(r"\textbackslash{}end")


def test_the_latex_publisher_stages_the_rubric_it_explains(tmp_path: Path) -> None:
    from inspect_audit._framework import stage_framework

    publisher = load_publisher("latex")
    assert publisher.stage is not None
    publisher.stage(tmp_path)
    # the audit's own setup staged it already: staging again leaves the same files
    stage_framework(tmp_path)
    assert json.loads((tmp_path / "framework/checks.json").read_text())["C.1"]["name"]


def test_a_hawk_excerpt_links_its_caption_to_the_sample_in_the_viewer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from inspect_audit.publishers import _excerpt
    from inspect_audit.publishers._excerpt import Excerpt, plain, resolve_all

    viewer = "https://viewer.example"
    address = "hawk:my-set-1/2026-01-01T00-00-00_task_X.eval"
    assert _excerpt.sample_link(address, "item 1", 2, "m#1", viewer) == (
        "https://viewer.example/eval-set/my-set-1#/logs/2026-01-01T00-00-00_task_X.eval"
        "/samples/sample/item%201/2/messages?message=m%231"
    )
    assert _excerpt.sample_link("/inputs/logs/0/a.eval", "a", 1, None, viewer) is None
    assert _excerpt.sample_link(address, "a", 1, None, None) is None
    monkeypatch.setenv("HAWK_LOG_VIEWER_URL", viewer + "/")
    assert _excerpt.viewer_base() == viewer

    _, manifest = read_bundle(FIXTURE)
    log = _transcript(tmp_path / "attempt.eval")
    manifest = _with_log(manifest, "/inputs/logs/0/attempt.eval", address)
    import inspect_audit._log_sources as sources

    def hawk_logs(address: str) -> list[str]:
        return [log]

    monkeypatch.setattr(sources, "hawk_logs", hawk_logs)
    excerpt = Excerpt(
        id="prompt",
        source=address,
        sample="item-1",
        epoch=2,
        message=0,
        caption="Mock model, item 1, attempt 2",
    )
    [quote] = resolve_all([excerpt], FIXTURE, manifest, viewer=viewer).values()
    assert quote.link is not None and quote.link.startswith(
        "https://viewer.example/eval-set/my-set-1#/logs/"
    )
    assert f"link {quote.link}" in plain(quote)
    box = latex.excerpt_box(quote)
    # the caption is the link; hyperref reads the URL's own # and % escaped
    assert box.startswith(r"\begin{iaquote}{\href{https://viewer.example/eval-set/my-set-1\#/logs/")
    assert r"}{Mock model, item 1, attempt 2}}\iaqpar{What is six plus seven?}" in box


def test_recorded_prose_is_set_as_paragraphs() -> None:
    lines = [
        "A sentence wrapped",
        "by its source.",
        "",
        "- an item",
        "- another with `code {x}`",
        "```",
        "  kept as is",
        "```",
    ]
    assert latex.prose(lines) == (
        r"\iaqpar{A sentence wrapped by its source.}"
        r"\iaqpar{- an item}"
        r"\iaqpar{- another with \iatt{code\ \{x\}}}"
        r"\iaqcode{2}{\ \ kept\ as\ is}"
    )


def test_supplied_logs_are_summarised_in_words_not_listed() -> None:
    notes = [
        (
            "2 of 4 logs produced by mc 0.1.192; mc 0.1.193 is installed, so the recorded "
            "grades come from a different build of the benchmark"
        ),
        (
            "1 of 4 logs produced by mc 0.1.200; mc 0.1.193 is installed, so the recorded "
            "grades come from a different build of the benchmark"
        ),
        (
            "1 of 4 logs produced by mc 0.1.96; mc 0.1.193 is installed, so the recorded "
            "grades come from a different build of the benchmark"
        ),
    ]
    logs = [f"hawk:set-{n}" for n in range(4)]
    assert latex.logs_in_words(logs, notes) == (
        "4 supplied, recorded by mc 0.1.96 (1), 0.1.192 (2), 0.1.200 (1); mc 0.1.193 is "
        "installed, so the recorded grades come from a different build of the benchmark"
    )
    assert latex.logs_in_words(logs, ["something else"]) == "4 supplied; something else"


def test_the_report_prints_no_log_ids_paths_or_hashes(tmp_path: Path) -> None:
    findings, manifest = read_bundle(FIXTURE)
    findings.subject["repo"] = "https://github.com/example/bench"
    findings.subject["supplied_logs"] = ["hawk:set-aaaa", "hawk:set-bbbb"]
    findings.subject["logs_provenance"] = [
        "2 of 2 logs produced by bench 1.0; bench 1.1 is installed"
    ]
    manifest = _with_log(manifest, "/inputs/logs/0/a.eval", "hawk:set-aaaa/a.eval")
    _, content = latex.source(findings, manifest, latex.Prose(), _example(tmp_path))
    printed = re.sub(r"\\href\{[^}]*\}", "", content)
    for ref in ("set-aaaa", "abc123456789", "evidence/reference.txt", "a.eval", "SHA-256"):
        assert ref not in printed
    assert (
        r"\dsfield{logs}{2 supplied, recorded by bench 1.0 (2); bench 1.1 is installed}" in content
    )
    # the repository links to the audited commit, which is not printed
    assert (
        r"\href{https://github.com/example/bench/tree/abc123456789}{example/\allowbreak{}bench}"
        in content
    )
    assert "1 recorded logs read" in content


def test_the_report_shows_short_frozen_fields_and_leaves_detail_in_the_findings(
    tmp_path: Path,
) -> None:
    """James, MirrorCode: evidence lists, full rationales, limitations, audit flags and
    hashes rendered as walls of text. The PDF shows short fields; the bundle keeps the rest."""
    findings, manifest = read_bundle(FIXTURE)
    findings.subject.update(
        ids="declared",
        items_read="before the agent started",
        task_args={"compaction_thresh": 300000},
        revision="819db252edc4e72feed767cb92b04ac2a5bc6792",
    )
    findings.auditor.limitations = ["A long limitation the scope prose summarises."]
    rating = findings.auditor.ratings["C.1"]
    rating.explanation = "The key is wrong, e.g. on item 3. A second sentence of detail."
    findings.auditor.findings[0].sample_ids = [f"item-{n}" for n in range(8)]
    upstream = _example(tmp_path)
    (upstream / "reports/Example/Example_Audit.tex").write_text(
        r"\reportno{GL-[number]} \evalscore{?} \commitref{[commit]}"
    )
    wrapper, content = latex.source(findings, manifest, latex.Prose(), upstream)
    for hidden in (
        "declared",
        "before the agent started",
        "compaction",
        "A long limitation",
        "A second sentence",
        "Headline evidence",
        "Report no.",
        r"\findingfield{Criterion}",
        *(e.observed for r in findings.auditor.ratings.values() for e in r.evidence),
    ):
        assert latex.tex(hidden) not in content, hidden
    assert "The key is wrong, e.g. on item 3." in content
    assert r"\findingfield{Samples}{" + latex.tex("8/3") + "}" in content
    assert r"\commitref{819db25}" in wrapper
    assert re.search(r"\\reportno\{GL-[0-9A-F]{6}\}", wrapper)


def test_first_sentence_keeps_abbreviations_and_numbers() -> None:
    assert latex.first_sentence("A vs. B differ at 0.5 s. Then more.") == "A vs. B differ at 0.5 s."
    assert latex.first_sentence("No full stop") == "No full stop"
    assert latex.affected([], 26).startswith("Benchmark-wide")
    assert latex.affected(["a", "b"], 26) == "2/26: a, b"


def test_comparisons_and_arrows_are_set_not_shown_as_question_marks() -> None:
    assert latex.tex(
        "\u226599% \u2192 x"
    ) == r"\ensuremath{\geq}99% \ensuremath{\rightarrow} x".replace("%", r"\%")
