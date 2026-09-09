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


def test_headless_defaults_and_explicit_cost_limit(tmp_path: Path) -> None:
    repo = str(git_repo(tmp_path / "repo"))
    target = investigate(repo, output_dir=str(tmp_path / "runs"))
    assert target.metadata["interactive"] is False
    assert target.cost_limit is None
    assert target.token_limit == 500_000
    assert target.token_limit_type == "output"
    limited = investigate(
        repo, output_dir=str(tmp_path / "runs"), enforce_cost_limit=True, budget_usd=7
    )
    assert limited.cost_limit == 7


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
    result = json.loads(asyncio.run(_investigate.investigation_budget(10)()))
    assert result["inspect_recorded_usd"] == (None if missing else 3)
    assert result["remaining_usd"] == (None if missing else 7)
    assert result["known_cost_subtotal_usd"] == (2 if missing else 3)
    assert result["unpriced_models"] == (["other"] if missing else [])


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
    import tarfile

    with tarfile.open(root / "inputs/source.tar") as archive:
        assert archive.getnames() == ["task.py"]
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
                "mkdir -p /workspace/source && tar -xf /inputs/source.tar -C /workspace/source && test -f /workspace/source/task.py && test ! -e /workspace/source/.env",
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
