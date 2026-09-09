"""Remote jobs: ledger, reservations, config shapes, and the tools against a fake Hawk."""

import asyncio
import json
from pathlib import Path

import pytest
import yaml

from inspect_audit import _jobs
from inspect_audit._investigate import Remote, jobs, run_audit, run_benchmark
from inspect_audit._jobs import JobLedger, audit_config, benchmark_config


class FakeHawk:
    """Stands in for the hawk CLI: records submissions, scripts eval status."""

    def __init__(self) -> None:
        self.submitted: list[Path] = []
        self.status = "running"
        self.stopped: list[str] = []

    def submit(self, config_path: Path) -> str:
        self.submitted.append(config_path)
        return f"set-{len(self.submitted)}"

    def evals(self, eval_set_id: str) -> list[dict[str, str]]:
        return [{"task": "t", "model": "m", "status": self.status, "samples": "2/2"}]

    def download(self, eval_set_id: str, out_dir: Path) -> list[Path]:
        out_dir.mkdir(parents=True, exist_ok=True)
        f = out_dir / "run.eval"
        f.write_bytes(b"x")
        return [f]

    def stop(self, eval_set_id: str) -> None:
        self.stopped.append(eval_set_id)


def remote(tmp_path: Path, allowance: float = 10.0) -> Remote:
    r = Remote(
        tmp_path, "https://hawk.example", None, "git+https://x/evals@abc", "git+https://x/audit@def",
        "ghcr.io/x/auditor@sha256:0", ["openai/gpt-5.6-luna", "openai/gpt-5-mini"], "bucket", None, allowance,
    )
    r.hawk = FakeHawk()  # type: ignore[assignment]
    return r


def run(coro):  # noqa: ANN001, ANN201
    return asyncio.run(coro)


def test_benchmark_submission_writes_config_reserves_and_records(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from inspect_audit import _investigate

    monkeypatch.setattr(_investigate, "_local_spend", lambda: (0.0, []))
    r = remote(tmp_path)
    out = run(run_benchmark(r, "inspect_evals/simpleqa_verified", tmp_path)(
        label="smoke", models=["openai/gpt-5.6-luna"], estimated_usd=1.5, limit=2, reasoning_effort="low", note="smoke"
    ))
    assert "set-1" in out
    job = JobLedger(tmp_path).get("smoke")
    assert job and job.kind == "benchmark" and job.estimated_usd == 1.5 and job.status == "submitted"
    config = yaml.safe_load(Path(job.config_path).read_text())
    assert config["tasks"][0]["name"] == "inspect_evals"
    assert config["tasks"][0]["items"][0]["name"] == "simpleqa_verified"
    assert config["models"][0]["name"] == "openrouter"
    assert config["models"][0]["items"][0]["args"]["config"]["reasoning_effort"] == "low"
    assert config["runner"]["environment"]["HAWK_RUNNER_REFRESH_URL"] == ""
    assert config["limit"] == 2 and config["packages"] == ["git+https://x/evals@abc"]


def test_disallowed_model_and_exhausted_allowance_are_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from inspect_ai.tool import ToolError

    from inspect_audit import _investigate

    monkeypatch.setattr(_investigate, "_local_spend", lambda: (0.0, []))
    r = remote(tmp_path, allowance=2.0)
    tool = run_benchmark(r, "pkg/task", tmp_path)
    with pytest.raises(ToolError, match="not an allowed worker model"):
        run(tool(label="aa", models=["openai/gpt-6-astra"], estimated_usd=0.5))
    run(tool(label="aa", models=["openai/gpt-5.6-luna"], estimated_usd=1.5))
    with pytest.raises(ToolError, match="cannot reserve"):
        run(tool(label="bb", models=["openai/gpt-5.6-luna"], estimated_usd=1.0))
    assert "already exists" in run(tool(label="aa", models=["openai/gpt-5.6-luna"], estimated_usd=0.1))
    assert len(r.hawk.submitted) == 1  # type: ignore[attr-defined]


def test_audit_over_a_previous_job_and_over_hawk_id(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from inspect_audit import _investigate

    monkeypatch.setattr(_investigate, "_local_spend", lambda: (0.0, []))
    r = remote(tmp_path)
    run(run_benchmark(r, "pkg/task", tmp_path)(label="gen", models=["openai/gpt-5.6-luna"], estimated_usd=1))
    out = run(run_audit(r, "pkg/task", tmp_path)(
        label="aud", logs="gen", items=["gold-answer"], auditor_model="openai/gpt-5.6-luna",
        grader_model="openai/gpt-5-mini", estimated_usd=2, limit=3, notes="general steer",
    ))
    assert "hawk:set-1" in out
    config = yaml.safe_load(Path(JobLedger(tmp_path).get("aud").config_path).read_text())  # type: ignore[union-attr]
    args = config["tasks"][0]["items"][0]["args"]
    assert args["logs"] == "hawk:set-1" and args["items"] == ["gold-answer"] and args["limit"] == 3
    assert config["model_roles"]["grader"]["items"][0]["name"] == "openai/gpt-5-mini"
    assert config["packages"] == ["git+https://x/audit@def", "git+https://x/evals@abc"]
    out = run(run_audit(r, "pkg/task", tmp_path)(
        label="aud2", logs="hawk:someone-elses-set", items=["gold-answer"], auditor_model="openai/gpt-5.6-luna", estimated_usd=1,
    ))
    assert "hawk:someone-elses-set" in out


def test_audit_over_supplied_inputs_stages_them_under_the_pinned_eval_set(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from inspect_audit import _investigate

    monkeypatch.setattr(_investigate, "_local_spend", lambda: (0.0, []))
    staged: list[tuple[str, str]] = []

    def fake_stage(local_dir, bucket, eval_set_id, profile):  # noqa: ANN001, ANN202
        staged.append((bucket, eval_set_id))
        return f"hawk:{eval_set_id}/inputs/logs"

    monkeypatch.setattr(_investigate, "stage_logs_to_s3", fake_stage)
    (tmp_path / "inputs" / "logs").mkdir(parents=True)
    r = remote(tmp_path)
    out = run(run_audit(r, "pkg/task", tmp_path)(label="in", logs="inputs", items=["gold-answer"], auditor_model="openai/gpt-5.6-luna", estimated_usd=1))
    assert staged and staged[0][0] == "bucket" and staged[0][1].startswith("inv-in-")
    config = yaml.safe_load(Path(JobLedger(tmp_path).get("in").config_path).read_text())  # type: ignore[union-attr]
    assert config["eval_set_id"] == staged[0][1]
    assert config["tasks"][0]["items"][0]["args"]["logs"] == f"hawk:{staged[0][1]}/inputs/logs"
    assert "inputs/logs" in out


def test_jobs_status_wait_collect_release_reservation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from inspect_audit import _investigate

    monkeypatch.setattr(_investigate, "_local_spend", lambda: (0.0, []))
    monkeypatch.setattr(_investigate, "usage_cost", lambda files: (0.42, {"openrouter/m": {"input": 1, "cache_read": 2, "output": 3}}))
    r = remote(tmp_path)
    (tmp_path / "inputs").mkdir()
    run(run_benchmark(r, "pkg/task", tmp_path)(label="jj", models=["openai/gpt-5.6-luna"], estimated_usd=3))
    tool = jobs(r, tmp_path)
    assert "running" in run(tool(action="status", label="jj"))
    from inspect_ai.tool import ToolError

    with pytest.raises(ToolError, match="not finished"):
        run(tool(action="collect", label="jj"))
    r.hawk.status = "success"  # type: ignore[attr-defined]
    monkeypatch.setattr(_jobs.time, "sleep", lambda s: None)
    assert "success" in run(tool(action="wait", label="jj", wait_minutes=1))
    out = run(tool(action="collect", label="jj"))
    assert "collected 1 log(s) to /inputs/jobs/jj/" in out and "$0.42" in out
    assert (tmp_path / "inputs" / "jobs" / "jj" / "run.eval").is_file()
    ledger = JobLedger(tmp_path)
    assert ledger.get("jj").actual_usd == 0.42 and ledger.reserved_usd() == 0  # type: ignore[union-attr]
    assert "jj (benchmark)" in run(tool(action="list"))
    run(tool(action="stop", label="jj"))
    assert r.hawk.stopped == ["set-1"]  # type: ignore[attr-defined]


def test_config_builders_produce_the_proven_shape() -> None:
    b = benchmark_config(
        name="inv-x", task_package="git+https://x/e@1", task_name="inspect_evals/simpleqa_verified",
        models=[{"model": "openai/gpt-5.6-luna", "reasoning_effort": None}], task_args=None, limit=None,
        sample_ids=["1", "2"], epochs=2, hawk_api_url="https://h",
    )
    assert b["tasks"][0]["items"][0]["args"] == {"sample_id": ["1", "2"]} and b["epochs"] == 2 and "limit" not in b
    a = audit_config(
        name="inv-a", eval_set_id=None, audit_package="git+https://x/a@2", task_package="git+https://x/e@1",
        audited_task="inspect_evals/simpleqa_verified", logs_source="hawk:s", items=["gold-answer"], limit=None,
        sample_ids=None, auditor={"model": "openai/gpt-5.6-luna"}, grader=None, auditor_image="img", notes=None,
        hawk_api_url="https://h",
    )
    assert "model_roles" not in a and "eval_set_id" not in a
    assert a["tasks"][0]["items"][0]["args"]["auditor_image"] == "img"


def test_hawk_cli_parsing(monkeypatch: pytest.MonkeyPatch) -> None:
    h = _jobs.Hawk("https://h", None)
    table = "Eval Set: x\n\nTask   Model   Status   Samples\n----  ----  ----  ----\naudit/bench/Chess Puzzles  gpt-5.6-terra  success   10/10\n"
    monkeypatch.setattr(h, "_run", lambda *a, **k: table)
    assert h.evals("x") == [{"task": "audit/bench/Chess Puzzles", "model": "gpt-5.6-terra", "status": "success", "samples": "10/10"}]
    monkeypatch.setattr(h, "_run", lambda *a, **k: "Eval set ID: inv-abc-123\nSee your eval set log: https://...")
    assert h.submit(Path("/tmp/c.yaml")) == "inv-abc-123"
    monkeypatch.setattr(h, "_run", lambda *a, **k: 'noise\n[{"id": "1", "status": "success"}]')
    assert h.samples("x") == [{"id": "1", "status": "success"}]
    assert json.dumps(_jobs.slug("Hello World! Big_Label")) == '"hello-world-big-label"'
