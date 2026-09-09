"""Exercise input isolation, publication and the real Docker/Quarto path."""

import asyncio
import importlib.util
import json
import subprocess
from pathlib import Path

import pytest
from inspect_ai import eval, task_with
from inspect_ai.model import (
    ModelCost,
    ModelInfo,
    ModelOutput,
    get_model,
    set_model_info,
)
from inspect_ai.solver import Generate, Solver, TaskState, solver
from inspect_ai.util import sandbox
from test_helpers.logs import run_fixture_eval

from inspect_audit._investigate import investigate, publish_report, save_publication
from inspect_audit._jobs import JobLedger


@pytest.fixture(autouse=True)
def _no_price_fetch(monkeypatch: pytest.MonkeyPatch) -> None:
    from inspect_audit import _investigate

    monkeypatch.setattr(_investigate, "register_openrouter_costs", lambda: 0)


def _snapshot_names(root: Path) -> list[str]:
    """Every file in the unpacked snapshot, relative to /inputs/source."""
    source = root / "inputs" / "source"
    return sorted(str(p.relative_to(source)) for p in source.rglob("*") if p.is_file())


def test_headless_defaults_enforce_the_allowance_without_a_token_cap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from inspect_audit import _investigate

    monkeypatch.setattr(_investigate, "register_openrouter_costs", lambda: 0)
    repo = str(git_repo(tmp_path / "repo"))
    target = investigate(repo, output_dir=str(tmp_path / "runs"))
    assert target.metadata["interactive"] is False
    assert target.cost_limit == 10
    assert target.token_limit is None
    planning = investigate(
        repo, output_dir=str(tmp_path / "runs"), enforce_cost_limit=False, token_limit="output:500k"
    )
    assert planning.cost_limit is None
    assert planning.token_limit == 500_000
    assert planning.token_limit_type == "output"


def test_snapshot_paths_paper_download_and_docs_mount(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import urllib.request

    from inspect_audit import _investigate

    monkeypatch.setattr(_investigate, "register_openrouter_costs", lambda: 0)
    repo = git_repo(tmp_path / "repo")
    (repo / "other_eval").mkdir()
    (repo / "other_eval" / "noise.py").write_text("# not under audit\n")
    subprocess.run(["git", "-C", str(repo), "add", "other_eval"], check=True)
    subprocess.run(["git", "-C", str(repo), "-c", "user.name=T", "-c", "user.email=t@e.org", "commit", "-qm", "more"], check=True)
    docs = tmp_path / "inspect-docs"
    docs.mkdir()
    (docs / "index.md").write_text("# docs\n")

    class Response:
        def __init__(self, body: bytes) -> None:
            self.body = body

        def read(self) -> bytes:
            return self.body

        def __enter__(self):  # noqa: ANN204
            return self

        def __exit__(self, *exc):  # noqa: ANN002
            return False

    fetched: list[str] = []

    def urlopen(request, timeout=None):  # noqa: ANN001
        fetched.append(request.full_url)
        return Response(b"%PDF-1.4 fake")

    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    target = investigate(
        str(repo),
        paths=["task.py"],
        paper="https://arxiv.org/abs/2509.07968v2",
        docs=[str(docs)],
        output_dir=str(tmp_path / "runs"),
    )
    root = Path(target.metadata["investigation_dir"])
    assert _snapshot_names(root) == ["task.py"]
    seed = json.loads((root / "inputs/seed.json").read_text())
    assert seed["paths"] == ["task.py"]
    assert fetched == ["https://arxiv.org/pdf/2509.07968v2"]
    assert seed["paper"] == "/inputs/paper/2509.07968v2.pdf"
    assert (root / "inputs/paper/2509.07968v2.pdf").read_bytes().startswith(b"%PDF")
    assert seed["docs"] == ["/inputs/docs/inspect-docs"]
    assert (root / "inputs/docs/inspect-docs/index.md").is_file()


@pytest.mark.parametrize("missing", [True, False])
def test_budget_does_not_turn_missing_prices_into_zero(
    monkeypatch: pytest.MonkeyPatch, missing: bool
) -> None:
    from inspect_ai.model import ModelUsage

    from inspect_audit import _investigate

    monkeypatch.setattr(
        _investigate,
        "sample_model_usage",
        lambda: {
            "priced": ModelUsage(
                input_tokens=1, output_tokens=1, total_tokens=2, total_cost=2
            ),
            "other": ModelUsage(
                input_tokens=1,
                output_tokens=1,
                total_tokens=2,
                total_cost=None if missing else 1,
            ),
        },
    )
    text = asyncio.run(_investigate.investigation_budget(10, True)())
    assert "Allowance: $10.00 (enforced)" in text
    if missing:
        assert "at least $2.00" in text and "other unpriced" in text
        assert "Remaining" not in text
    else:
        assert "Spent: $3.00   Remaining: $7.00" in text


def test_findings_validate_and_bundle_input_evidence(tmp_path: Path) -> None:
    from inspect_audit._report import validate_findings

    report = tmp_path / "work/report"
    inputs = tmp_path / "inputs"
    report.mkdir(parents=True)
    inputs.mkdir()
    (inputs / "trace.txt").write_text("primary evidence")
    (report / "report.qmd").write_text("Report")
    (report / "report.html").write_text("<p>Report</p>")
    finding = dict(
        id="F1",
        section="grader",
        claim="A claim",
        status="supported",
        origin="historical",
        evidence=[dict(path="/inputs/trace.txt", location="line 1")],
        reproduce="Read line 1",
        limitations="",
    )
    register = report / "findings.json"
    register.write_text(json.dumps([finding]))
    destination = save_publication(tmp_path)
    saved = json.loads((destination / "findings.json").read_text())
    assert (
        destination / saved[0]["evidence"][0]["path"]
    ).read_text() == "primary evidence"
    assert (
        json.loads(register.read_text())[0]["evidence"][0]["path"]
        == "/inputs/trace.txt"
    )
    for change in [
        dict(status="confirmed"),
        dict(section="vibes"),
        dict(evidence=[]),
        dict(evidence=[dict(path="../escape", location="line 1")]),
        dict(evidence=[dict(path="/etc/passwd", location="line 1")]),
        dict(evidence=[dict(path="missing", location="line 1")]),
    ]:
        register.write_text(json.dumps([{**finding, **change}]))
        with pytest.raises(ValueError):
            validate_findings(tmp_path)
    register.write_text(json.dumps([{"id": "missing-fields"}]))
    with pytest.raises(ValueError):
        validate_findings(tmp_path)
    register.write_text(json.dumps([finding, finding]))
    with pytest.raises(ValueError, match="unique"):
        validate_findings(tmp_path)


def test_log_inputs_share_storage_without_a_second_copy(tmp_path: Path) -> None:
    source = tmp_path / "source.eval"
    source.write_bytes(b"log content")
    target = investigate(
        str(git_repo(tmp_path / "repo")),
        logs=[str(source)],
        output_dir=str(tmp_path / "runs"),
    )
    root = Path(target.metadata["investigation_dir"])
    assert (root / "inputs/logs/0/source.eval").stat().st_ino == source.stat().st_ino
    assert not (root / "work/findings.json").exists()
    assert (root / "work/report/findings.json").is_file()


def git_repo(path: Path) -> Path:
    path.mkdir()
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    (path / "task.py").write_text("# benchmark source\n")
    subprocess.run(["git", "-C", str(path), "add", "task.py"], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(path),
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.org",
            "commit",
            "-qm",
            "fixture",
        ],
        check=True,
    )
    (path / ".env").write_text("DO_NOT_STAGE=secret")
    return path


def test_snapshot_excludes_untracked_secrets_and_retains_publications(
    tmp_path: Path,
) -> None:
    target = investigate(
        str(git_repo(tmp_path / "repo")), output_dir=str(tmp_path / "runs")
    )
    root = Path(target.metadata["investigation_dir"])
    assert _snapshot_names(root) == ["task.py"]
    seed = json.loads((root / "inputs/seed.json").read_text())
    assert len(seed["revision"]) == 40
    report = root / "work/report"
    (report / "report.html").write_text("first version")
    first = save_publication(root)
    (report / "report.html").write_text("second version")
    second = save_publication(root)
    assert first != second
    assert (first / "report.html").read_text() == "first version"
    (report / "leak").symlink_to(tmp_path / "repo/.env")
    with pytest.raises(ValueError, match="symlinks"):
        save_publication(root)


def test_invalid_seed_and_budget_fail_early(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="finite and positive"):
        investigate("https://example.org/repo", budget_usd=float("nan"))
    with pytest.raises(ValueError, match="Local log source"):
        investigate(
            "https://example.org/repo",
            logs=[str(tmp_path / "missing")],
            output_dir=str(tmp_path),
        )


def test_transcript_escapes_untrusted_html() -> None:
    from inspect_audit._investigate import ASSETS

    spec = importlib.util.spec_from_file_location(
        "components", ASSETS / "report/components.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    rendered = module.transcript("<assistant>", "<script>alert(1)</script>", "x&y")
    assert "<script>" not in rendered
    assert "&lt;script&gt;" in rendered
    assert "x&amp;y" in rendered


def test_publication_switches_from_work_to_discussion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from inspect_ai.agent import AgentState
    from inspect_ai.model import ChatMessageAssistant, ModelOutput

    from inspect_audit import _investigate

    record = _investigate.InvestigationState()
    monkeypatch.setattr(_investigate, "store_as", lambda _: record)
    state = AgentState(messages=[ChatMessageAssistant(content="Working")])
    state.output = ModelOutput.from_content("mockllm/model", "Working")
    assert isinstance(asyncio.run(_investigate._continue(state, True)), str)
    record.published = "/published/version1"
    assert asyncio.run(_investigate._continue(state, False)) is False

    async def operator_reply(state: AgentState) -> str:
        return "Show me the supporting transcript."

    monkeypatch.setattr(_investigate, "_operator_turn", operator_reply)
    assert (
        asyncio.run(_investigate._continue(state, True))
        == "Show me the supporting transcript."
    )


@solver
def render_probe(root: str) -> Solver:
    async def solve(state: TaskState, generate: Generate) -> TaskState:
        result = await sandbox().exec(
            [
                "bash",
                "-c",
                "test -f /inputs/source/task.py && test ! -e /inputs/source/.env && test ! -w /inputs/source/task.py",
            ]
        )
        assert result.success, result.stderr
        result = await sandbox().exec(
            [
                "python",
                "-c",
                "import sys; sys.path.insert(0, '/workspace/report'); from components import bar_chart; bar_chart(['Reviewed', 'Unreviewed'], [3, 1], '/workspace/report/coverage.png', ylabel='Attempts')",
            ]
        )
        assert result.success, result.stderr
        await sandbox().write_file(
            "/workspace/report/report.qmd",
            "---\ntitle: Smoke audit\nformat:\n  html:\n    embed-resources: true\n---\n\n## Findings\n\nNo model conclusions: infrastructure test only.\n\n![Coverage](coverage.png)\n",
        )
        receipt = await publish_report(root)()
        state.store.set("publication_receipt", receipt)
        return state

    return solve


@pytest.mark.docker
def test_real_container_renders_and_exports_report(tmp_path: Path) -> None:
    set_model_info(
        "mockllm/model",
        ModelInfo(
            cost=ModelCost(input=0, output=0, input_cache_read=0, input_cache_write=0)
        ),
    )
    target = investigate(
        str(git_repo(tmp_path / "repo")),
        output_dir=str(tmp_path / "runs"),
        interactive=False,
    )
    root = Path(target.metadata["investigation_dir"])
    target = task_with(target, solver=render_probe(str(root)))
    log = eval(
        target, model="mockllm/model", display="none", log_dir=str(tmp_path / "logs")
    )[0]
    assert log.status == "success", log.error
    published = list((root / "published").glob("*/report.html"))
    assert len(published) == 1
    assert "Smoke audit" in published[0].read_text()
    assert "data:image/png;base64," in published[0].read_text()


@pytest.mark.docker
def test_agent_reads_logs_and_publishes_before_batch_exit(tmp_path: Path) -> None:
    set_model_info(
        "mockllm/model",
        ModelInfo(
            cost=ModelCost(input=0, output=0, input_cache_read=0, input_cache_write=0)
        ),
    )
    source_log = run_fixture_eval(str(tmp_path / "source_logs"))
    target = investigate(
        str(git_repo(tmp_path / "repo")),
        logs=[source_log],
        output_dir=str(tmp_path / "runs"),
        interactive=False,
    )
    script = """python - <<'PY'
import json
from pathlib import Path
from inspect_ai.log import read_eval_log
seed = json.loads(Path('/inputs/seed.json').read_text())
log = read_eval_log(seed['logs'][0]['staged'])
assert len(log.samples) == 3
Path('/workspace/report/report.qmd').write_text('---\\ntitle: Agent smoke audit\\nformat: html\\n---\\n\\nRead three recorded samples.\\n')
PY"""
    model = get_model(
        "mockllm/model",
        custom_outputs=[
            ModelOutput.for_tool_call(
                "mockllm/model", "skill", {"command": "investigating"}
            ),
            ModelOutput.for_tool_call("mockllm/model", "bash", {"command": script}),
            ModelOutput.for_tool_call("mockllm/model", "budget", {}),
            ModelOutput.for_tool_call("mockllm/model", "publish_report", {}),
        ],
    )
    log = eval(target, model=model, display="none", log_dir=str(tmp_path / "logs"))[0]
    assert log.status == "success", log.error
    root = Path(target.metadata["investigation_dir"])
    published = list((root / "published").glob("*/report.html"))
    assert len(published) == 1
    assert "Read three recorded samples" in published[0].read_text()
    assert log.samples and log.samples[0].store
    from inspect_ai.model import ChatMessageTool

    assert not [
        m.error
        for m in log.samples[0].messages
        if isinstance(m, ChatMessageTool) and m.error
    ]


def test_publish_lint_catches_dashes_comments_and_process_narration() -> None:
    from inspect_audit._report import lint_report_text, lint_report_warnings

    bad = (
        "I reviewed the logs. I inspected the grader. I examined the paper. I checked the "
        "config \u2014 carefully. " + " ".join(["word"] * 45) + ". <!-- draft -->"
    )
    problems = lint_report_text(bad)
    assert any("dash" in p for p in problems)
    assert any("drafting comments" in p for p in problems)
    assert any("narrate" in p for p in problems)
    # a long sentence is a note, not a refusal to publish
    assert not any("over 40 words" in p for p in problems)
    assert any("over 40 words" in w for w in lint_report_warnings(bad))
    assert lint_report_text("Claude Haiku 4.5 abstained on 812 of 1,000 attempts.") == []


def test_lint_reads_prose_only_not_tables_code_or_quoted_evidence() -> None:
    """Evidence must never be reworded to satisfy a style rule."""
    from inspect_audit._report import (
        _prose_text,
        lint_report_text,
        lint_report_warnings,
    )

    html = (
        '<div id="TOC"><ul>'
        + "".join(f"<li>Section {i} of this report</li>" for i in range(20))
        + "</ul></div>"
        '<nav><a href="#x">skip</a></nav>'
        "<p>The grader accepted 812 of 1,000 attempts.</p>"
        "<table><tr><td>" + "</td><td>".join(["cell"] * 60) + "</td></tr></table>"
        "<blockquote>the model wrote \u2014 with an em dash \u2014 exactly this</blockquote>"
        "<pre><code>df = df[df.score \u2014 1]</code></pre>"
        "<figcaption>Figure 1 \u2014 abstentions by model</figcaption>"
    )
    prose = _prose_text(html)
    assert "812 of 1,000" in prose
    assert "cell" not in prose and "em dash" not in prose and "df = df" not in prose
    assert "Section 7" not in prose, "the table of contents is navigation, not prose"
    assert lint_report_text(prose) == []
    assert lint_report_warnings(prose) == []


def test_the_same_input_cited_twice_publishes_once(tmp_path: Path) -> None:
    report = tmp_path / "work/report"
    inputs = tmp_path / "inputs"
    report.mkdir(parents=True)
    inputs.mkdir()
    (inputs / "paper.pdf").write_bytes(b"%PDF")
    (report / "report.qmd").write_text("Report")
    (report / "report.html").write_text("<p>Report</p>")
    finding = dict(
        id="F1", section="task", claim="A", status="supported", origin="source",
        evidence=[dict(path="/inputs/paper.pdf", location="p. 1"), dict(path="/inputs/paper.pdf", location="p. 2")],
        reproduce="read", limitations="",
    )
    (report / "findings.json").write_text(json.dumps([finding, {**finding, "id": "F2"}]))
    destination = save_publication(tmp_path)
    saved = json.loads((destination / "findings.json").read_text())
    assert {e["path"] for f in saved for e in f["evidence"]} == {"_inputs/paper.pdf"}
    assert (destination / "_inputs/paper.pdf").read_bytes() == b"%PDF"


def test_mounted_skills_are_wellformed_and_adapted() -> None:
    """Every mounted skill parses, is named after its directory, and says where it is.

    The nine borrowed skills were written for a repo checkout with a user to ask. Each
    keeps its original text and gains an `In this container` section; the container has
    no `uv`, so a stray `uv run` would send the agent down a dead end.
    """
    from inspect_audit._investigate import ASSETS, INVESTIGATION_SKILLS

    ours = {"investigating", "writing"}
    for name in INVESTIGATION_SKILLS:
        text = (ASSETS / "skills" / name / "SKILL.md").read_text()
        import yaml

        front = yaml.safe_load(text.split("---")[1])
        assert front["name"] == name, name
        assert front["description"], name
        if name not in ours:
            assert "## In this container" in text, name
            leftovers = [
                line
                for line in text.splitlines()
                if "uv run" in line and "gnore" not in line
            ]
            assert not leftovers, (name, leftovers)


def test_resume_reuses_the_directory_ledger_and_staged_logs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A restarted investigator must not stage its inputs again or lose its jobs."""
    from inspect_audit import _investigate

    staged: list[str] = []
    monkeypatch.setattr(
        _investigate,
        "stage_logs_to_s3",
        lambda local_dir, bucket, eval_set_id, profile: staged.append(eval_set_id)
        or f"hawk:{eval_set_id}/inputs/logs",
    )
    repo = str(git_repo(tmp_path / "repo"))
    log = run_fixture_eval(str(tmp_path / "logs"))
    common = dict(
        logs=[str(log)],
        hawk_api_url="https://hawk.example",
        task_package="git+https://github.com/x/inspect_evals@abc",
        output_dir=str(tmp_path / "runs"),
    )
    first = investigate(repo, **common)  # type: ignore[arg-type]
    root = Path(first.metadata["investigation_dir"])
    (root / "jobs.json").write_text(
        json.dumps(
            [
                {
                    "label": "smoke",
                    "kind": "eval-set",
                    "eval_set_id": "inv-smoke-1234abcd",
                    "config_path": str(root / "jobs" / "smoke.eval-set.yaml"),
                    "submitted_at": "2026-09-09T00:00:00+00:00",
                    "estimated_usd": 1.0,
                    "reserved_usd": 2.0,
                    "status": "submitted",
                }
            ]
        )
    )
    assert len(staged) == 1

    # a job left pending by an interrupted run: resume must settle it with Hawk before
    # the agent starts, or it is either lost or launched twice
    ledger_before = json.loads((root / "jobs.json").read_text())
    ledger_before[0]["status"] = "pending"
    (root / "jobs.json").write_text(json.dumps(ledger_before))
    asked: list[str] = []

    class RecordingHawk:
        def __init__(self, *args: object, **kwargs: object) -> None:
            pass

        def eval_set_exists(self, eval_set_id: str) -> bool:
            asked.append(eval_set_id)
            return True

    monkeypatch.setattr(_investigate, "Hawk", RecordingHawk)
    second = investigate(repo, resume=str(root), **common)  # type: ignore[arg-type]
    assert asked == ["inv-smoke-1234abcd"], "resume did not ask Hawk about the pending job"
    assert JobLedger(root).get("smoke").status == "submitted"  # type: ignore[union-attr]
    assert Path(second.metadata["investigation_dir"]) == root
    assert len(staged) == 1, "the supplied logs were staged once, at first setup"
    ledger = JobLedger(root)
    assert [j.label for j in ledger.jobs] == ["smoke"] and ledger.reserved_usd() == 2.0

    with pytest.raises(ValueError, match="not an investigation directory"):
        investigate(repo, resume=str(tmp_path / "nowhere"), **common)  # type: ignore[arg-type]


def test_a_remote_reservation_stops_the_investigator_spending_the_same_money_locally(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Inspect's cost limit only counts this agent's own calls, so the tools must count both."""
    from inspect_audit import _investigate
    from inspect_audit._investigate import Remote, _continue
    from inspect_audit._jobs import Job

    (tmp_path / "work").mkdir()
    monkeypatch.setattr(_investigate, "_local_spend", lambda: (3.0, []))
    r = Remote(tmp_path, "https://hawk.example", None, "pkg", "pkg", "img", ["m"], "bucket", None, 10.0)
    assert r.over_allowance() is None

    with r.ledger.transaction() as ledger:
        ledger.add(
            Job(label="big", kind="eval-set", eval_set_id="inv-big-1", config_path="x",
                submitted_at="now", estimated_usd=5.0, reserved_usd=8.0, status="submitted")
        )
    message = r.over_allowance()
    assert message is not None and "$11.00" in message and "$8.00" in message

    class _State:
        pass

    from inspect_ai.util._store import Store, init_subtask_store

    init_subtask_store(Store())  # a fresh sample store: nothing published yet
    told = asyncio.run(_continue(_State(), interactive=False, remote=r))  # type: ignore[arg-type]
    assert isinstance(told, str) and "Publish the report now" in told


def test_a_resumed_investigation_remembers_what_it_already_spent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from inspect_audit import _investigate
    from inspect_audit._investigate import Remote

    (tmp_path / "work").mkdir()
    monkeypatch.setattr(_investigate, "_local_spend", lambda: (2.0, []))
    first = Remote(tmp_path, "https://hawk.example", None, "pkg", "pkg", "img", ["m"], "bucket", None, 10.0)
    first.record_local_spend()
    assert first.local_usd() == 2.0

    # a second run of the same investigation: Inspect's usage starts from zero again
    monkeypatch.setattr(_investigate, "_local_spend", lambda: (1.5, []))
    resumed = Remote(tmp_path, "https://hawk.example", None, "pkg", "pkg", "img", ["m"], "bucket", None, 10.0)
    assert resumed.prior_local_usd == 2.0
    assert resumed.local_usd() == 3.5, "the earlier run's spend must still count against the allowance"
