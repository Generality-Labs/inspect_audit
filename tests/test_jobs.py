"""Remote jobs: the policy over agent-written configs, staging, submission, ledger, collection."""

import asyncio
import json
from pathlib import Path

import pytest
import yaml

from inspect_audit import _jobs
from inspect_audit._investigate import Remote, hawk_submit, jobs
from inspect_audit._jobs import JobLedger, Policy, task_package_name, validate_config

TASK_PKG = "git+https://github.com/UKGovernmentBEIS/inspect_evals@abc"
AUDIT_PKG = "git+https://github.com/Generality-Labs/inspect_audit@def"
IMAGE = "ghcr.io/x/auditor@sha256:0"
HAWK = "https://hawk.example"
EXAMPLES = Path(__file__).parent.parent / "src/inspect_audit/investigation/skills/investigating/examples"


class FakeHawk:
    def __init__(self) -> None:
        self.submitted: list[Path] = []
        self.eval_status = "running"
        self.stopped: list[str] = []

    def submit(self, config_path: Path) -> str:
        self.submitted.append(config_path)
        return f"set-{len(self.submitted)}"

    def evals(self, eval_set_id: str) -> list[dict[str, str]]:
        return [{"task": "t", "model": "m", "status": self.eval_status, "samples": "2/2"}]

    def download(self, eval_set_id: str, out_dir: Path) -> list[Path]:
        out_dir.mkdir(parents=True, exist_ok=True)
        f = out_dir / "run.eval"
        f.write_bytes(b"x")
        return [f]

    def stop(self, eval_set_id: str) -> None:
        self.stopped.append(eval_set_id)

    def watch(self, eval_set_id: str) -> str:
        return "sample 1: running, 2 retries\n⚠ pods can't be scheduled"

    def trace(self, eval_set_id: str, lines: int = 100) -> str:
        return "enter generate ...\n"

    def stacktrace(self, eval_set_id: str) -> str:
        return "Thread 1: asyncio ...\n"

    def status(self, eval_set_id: str) -> str:
        return '{"pods": []}'

    def samples(self, eval_set_id: str, limit: int = 500) -> list[dict[str, object]]:
        return [{"uuid": "u-1", "task": "t", "model": "m", "status": "success", "score": 1}]

    def transcript(self, sample_uuid: str, out_dir: Path) -> Path:
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / f"{sample_uuid}.md"
        path.write_text("# transcript")
        return path

    def transcripts(self, eval_set_id: str, out_dir: Path, limit: int | None = None) -> list[Path]:
        out_dir.mkdir(parents=True, exist_ok=True)
        paths = []
        for i in range(limit or 2):
            f = out_dir / f"s-{i}.md"
            f.write_text("# transcript")
            paths.append(f)
        return paths


def remote(tmp_path: Path, allowance: float = 10.0) -> Remote:
    (tmp_path / "work").mkdir(exist_ok=True)
    r = Remote(tmp_path, HAWK, None, TASK_PKG, AUDIT_PKG, IMAGE,
               ["openai/gpt-5.6-luna", "openai/gpt-5-mini"], "bucket", None, allowance)
    r.hawk = FakeHawk()  # type: ignore[assignment]
    return r


def run(coro):  # noqa: ANN001, ANN201
    return asyncio.run(coro)


def filled_example(filename: str, **overrides) -> dict:  # noqa: ANN003
    text = (EXAMPLES / filename).read_text()
    text = (
        text.replace("<remote.task_package>", TASK_PKG)
        .replace("<remote.audit_package>", AUDIT_PKG)
        .replace("<remote.auditor_image>", IMAGE)
        .replace("<remote.hawk>", HAWK)
        .replace("<registry package, e.g. inspect_evals>", "inspect_evals")
        .replace("<task, e.g. simpleqa_verified>", "simpleqa_verified")
        .replace("<registry name of the audited task, e.g. inspect_evals/simpleqa_verified>", "inspect_evals/simpleqa_verified")
        .replace("<remote.supplied_logs, or hawk:<eval set id of your job>>", "hawk:inv-staged-abc/inputs/logs")
        .replace("<id inside remote.supplied_logs, when auditing the supplied logs>   # otherwise omit", "inv-staged-abc")
    )
    config = yaml.safe_load(text)
    config.update(overrides)
    return config


def policy() -> Policy:
    return Policy(packages=[TASK_PKG, AUDIT_PKG], task_names=["inspect_evals", "inspect_audit"],
                  models=["openai/gpt-5.6-luna", "openai/gpt-5-mini"], auditor_images=[IMAGE], hawk_api_url=HAWK)


def test_the_example_configs_pass_the_policy_once_filled_in() -> None:
    assert validate_config(filled_example("benchmark.eval-set.yaml"), policy(), set()) == []
    assert validate_config(filled_example("audit.eval-set.yaml"), policy(), {"inv-staged-abc"}) == []


@pytest.mark.parametrize(
    "change, expect",
    [
        ({"packages": ["git+https://evil/x"]}, "package not allowed"),
        ({"runner": {"image": "evil:latest", "environment": {"HAWK_API_URL": HAWK, "HAWK_RUNNER_REFRESH_URL": ""}, "secrets": [{"name": "OPENROUTER_API_KEY"}]}}, "runner keys not allowed"),
        ({"runner": {"environment": {"HAWK_API_URL": HAWK, "HAWK_RUNNER_REFRESH_URL": "", "AWS_SECRET": "x"}, "secrets": [{"name": "OPENROUTER_API_KEY"}]}}, "environment keys not allowed"),
        ({"runner": {"environment": {"HAWK_API_URL": HAWK, "HAWK_RUNNER_REFRESH_URL": ""}, "secrets": [{"name": "HF_TOKEN"}]}}, "secret not allowed"),
        ({"models": [{"package": "openai", "name": "openrouter", "items": [{"name": "openai/gpt-6-astra", "args": {"base_url": "https://openrouter.ai/api/v1"}}]}]}, "model not allowed"),
        ({"models": [{"package": "openai", "name": "openrouter", "items": [{"name": "openai/gpt-5.6-luna", "args": {"base_url": "https://evil/v1"}}]}]}, "base_url"),
        ({"agents": [{"package": "git+https://evil/a", "name": "a", "items": [{"name": "x"}]}]}, "keys not allowed"),
        ({"limit": 5000}, "exceeds the cap"),
        ({"eval_set_id": "someone-elses"}, "eval_set_id must start"),
        ({"tasks": [{"package": TASK_PKG, "name": "inspect_evals", "items": [{"name": "simpleqa_verified", "args": {"sandbox": "docker"}}]}]}, "task arg not allowed"),
    ],
)
def test_policy_refuses_each_escape(change: dict, expect: str) -> None:
    problems = validate_config(filled_example("benchmark.eval-set.yaml", **change), policy(), set())
    assert any(expect in p for p in problems), problems


def test_policy_requires_a_size_and_known_log_sources() -> None:
    config = filled_example("benchmark.eval-set.yaml")
    del config["limit"]
    assert any("states its size" in p for p in validate_config(config, policy(), set()))
    audit = filled_example("audit.eval-set.yaml")
    assert any("staged or ran" in p for p in validate_config(audit, policy(), set()))
    assert validate_config(audit, policy(), {"inv-staged-abc"}) == []


def test_task_package_name() -> None:
    assert task_package_name(TASK_PKG) == "inspect_evals"
    assert task_package_name("inspect-evals==1.2") == "inspect_evals"
    assert task_package_name("git+https://github.com/x/epoch_bench.git@main") == "epoch_bench"


def _write(r_root: Path, name: str, config: dict) -> str:
    (r_root / "work" / "jobs").mkdir(parents=True, exist_ok=True)
    (r_root / "work" / "jobs" / name).write_text(yaml.safe_dump(config))
    return f"/workspace/jobs/{name}"


def test_submit_reserves_records_and_refuses_duplicates_and_overspend(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from inspect_ai.tool import ToolError

    from inspect_audit import _investigate

    monkeypatch.setattr(_investigate, "_local_spend", lambda: (0.0, []))
    r = remote(tmp_path, allowance=3.0)
    path = _write(tmp_path, "smoke.eval-set.yaml", filled_example("benchmark.eval-set.yaml"))
    out = run(hawk_submit(r, tmp_path)(config=path, estimated_usd=2.0, note="smoke"))
    assert "set-1" in out
    job = JobLedger(tmp_path).get("smoke-luna")
    assert job and job.estimated_usd == 2.0 and job.status == "submitted"
    assert Path(job.config_path).is_file() and "set-1" in json.loads((tmp_path / "log_sources.json").read_text())
    assert "already exists" in run(hawk_submit(r, tmp_path)(config=path, estimated_usd=0.5))
    other = _write(tmp_path, "two.eval-set.yaml", filled_example("benchmark.eval-set.yaml", name="inv-two"))
    with pytest.raises(ToolError, match="cannot reserve"):
        run(hawk_submit(r, tmp_path)(config=other, estimated_usd=1.5))
    bad = _write(tmp_path, "bad.eval-set.yaml", filled_example("benchmark.eval-set.yaml", name="inv-bad", packages=["git+https://evil/x"]))
    with pytest.raises(ToolError, match="config refused"):
        run(hawk_submit(r, tmp_path)(config=bad, estimated_usd=0.1))
    with pytest.raises(ToolError, match="under /workspace"):
        run(hawk_submit(r, tmp_path)(config="/etc/passwd", estimated_usd=0.1))
    assert len(r.hawk.submitted) == 1  # type: ignore[attr-defined]


def test_audit_over_supplied_logs_uses_the_staged_source_only(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from inspect_ai.tool import ToolError

    from inspect_audit import _investigate

    monkeypatch.setattr(_investigate, "_local_spend", lambda: (0.0, []))
    r = remote(tmp_path)
    r.known_sources.add("inv-staged-abc")  # what investigate() records after staging at setup
    config = filled_example("audit.eval-set.yaml")
    assert "set-1" in run(hawk_submit(r, tmp_path)(config=_write(tmp_path, "audit.eval-set.yaml", config), estimated_usd=1.0))
    foreign = filled_example("audit.eval-set.yaml", name="inv-foreign")
    del foreign["eval_set_id"]
    foreign["tasks"][0]["items"][0]["args"]["logs"] = "hawk:someone-elses-set"
    with pytest.raises(ToolError, match="staged or ran"):
        run(hawk_submit(r, tmp_path)(config=_write(tmp_path, "f.eval-set.yaml", foreign), estimated_usd=1.0))


def test_supplied_logs_are_staged_at_setup_not_by_the_agent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from inspect_audit import _investigate

    monkeypatch.setattr(_investigate, "register_openrouter_costs", lambda: 0)
    calls: list[tuple[str, str]] = []

    def fake_stage(local_dir, bucket, eval_set_id, profile):  # noqa: ANN001, ANN202
        calls.append((bucket, eval_set_id))
        return f"hawk:{eval_set_id}/inputs/logs"

    monkeypatch.setattr(_investigate, "stage_logs_to_s3", fake_stage)
    repo = tmp_path / "repo"
    repo.mkdir()
    import subprocess

    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    (repo / "t.py").write_text("x")
    subprocess.run(["git", "-C", str(repo), "add", "t.py"], check=True)
    subprocess.run(["git", "-C", str(repo), "-c", "user.name=T", "-c", "user.email=t@e.org", "commit", "-qm", "c"], check=True)
    log = tmp_path / "a.eval"
    log.write_bytes(b"x")
    target = _investigate.investigate(
        str(repo), logs=[str(log)], output_dir=str(tmp_path / "runs"), enforce_cost_limit=False,
        hawk_api_url=HAWK, task_package=TASK_PKG,
    )
    root = Path(target.metadata["investigation_dir"])
    seed = json.loads((root / "inputs/seed.json").read_text())
    assert calls and calls[0][0] == "arcadia-impact-generality-inspect" and calls[0][1].startswith("inv-inputs-")
    assert seed["remote"]["supplied_logs"] == f"hawk:{calls[0][1]}/inputs/logs"
    assert calls[0][1] in json.loads((root / "log_sources.json").read_text())
    assert "hawk_jobs" in target.metadata["capabilities"]
    names = {t.__name__ if hasattr(t, "__name__") else str(t) for t in []}
    assert names == set()


def test_jobs_status_wait_collect_release_reservation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from inspect_ai.tool import ToolError

    from inspect_audit import _investigate

    monkeypatch.setattr(_investigate, "_local_spend", lambda: (0.0, []))
    monkeypatch.setattr(_investigate, "usage_cost", lambda files: (0.42, {"openrouter/m": {"input": 1, "cache_read": 2, "output": 3}}))
    r = remote(tmp_path)
    (tmp_path / "inputs").mkdir()
    run(hawk_submit(r, tmp_path)(config=_write(tmp_path, "j.eval-set.yaml", filled_example("benchmark.eval-set.yaml", name="inv-jj")), estimated_usd=3))
    tool = jobs(r, tmp_path)
    assert "running" in run(tool(action="evals", label="jj"))
    with pytest.raises(ToolError, match="not finished"):
        run(tool(action="collect", label="jj"))
    r.hawk.eval_status = "success"  # type: ignore[attr-defined]
    monkeypatch.setattr(_jobs.time, "sleep", lambda s: None)
    assert "success" in run(tool(action="wait", label="jj", wait_minutes=1))
    out = run(tool(action="collect", label="jj"))
    assert "collected 1 log(s) to /inputs/jobs/jj/" in out and "$0.42" in out
    assert (tmp_path / "inputs" / "jobs" / "jj" / "run.eval").is_file()
    ledger = JobLedger(tmp_path)
    assert ledger.get("jj").actual_usd == 0.42 and ledger.reserved_usd() == 0  # type: ignore[union-attr]
    assert "jj (eval-set)" in run(tool(action="list"))
    run(tool(action="stop", label="jj"))
    assert r.hawk.stopped == ["set-1"]  # type: ignore[attr-defined]
    r.hawk.logs = lambda eval_set_id, lines=120: "uv pip install ... ok\nRunning Inspect eval-set"  # type: ignore[attr-defined]
    assert "Running Inspect eval-set" in run(tool(action="logs", label="jj"))


def test_jobs_babysitting_actions_are_read_only(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """watch/trace/stacktrace/status/samples/transcripts observe a job, they do not change it."""
    from inspect_ai.tool import ToolError

    from inspect_audit import _investigate

    monkeypatch.setattr(_investigate, "_local_spend", lambda: (0.0, []))
    r = remote(tmp_path)
    (tmp_path / "inputs").mkdir()
    run(hawk_submit(r, tmp_path)(config=_write(tmp_path, "j.eval-set.yaml", filled_example("benchmark.eval-set.yaml", name="inv-jj")), estimated_usd=3))
    tool = jobs(r, tmp_path)
    assert "pods can't be scheduled" in run(tool(action="watch", label="jj"))
    assert "enter generate" in run(tool(action="trace", label="jj"))
    assert "asyncio" in run(tool(action="stacktrace", label="jj"))
    assert "pods" in run(tool(action="status", label="jj"))
    assert "u-1" in run(tool(action="samples", label="jj"))
    with pytest.raises(ToolError, match="needs sample="):
        run(tool(action="transcript", label="jj"))
    assert "u-1.md" in run(tool(action="transcript", label="jj", sample="u-1"))
    assert (tmp_path / "inputs" / "jobs" / "jj" / "transcripts" / "u-1.md").is_file()
    assert "3 transcript(s)" in run(tool(action="transcripts", label="jj", limit=3))
    with pytest.raises(ToolError, match="action must be"):
        run(tool(action="delete", label="jj"))
    assert r.hawk.stopped == []  # type: ignore[attr-defined]
    assert JobLedger(tmp_path).get("jj").status == "submitted"  # type: ignore[union-attr]


def test_hawk_cli_parsing(monkeypatch: pytest.MonkeyPatch) -> None:
    h = _jobs.Hawk("https://h", None)
    table = "Eval Set: x\n\nTask   Model   Status   Samples\n----  ----  ----  ----\naudit/bench/Chess Puzzles  gpt-5.6-terra  success   10/10\n"
    monkeypatch.setattr(h, "_run", lambda *a, **k: table)
    assert h.evals("x") == [{"task": "audit/bench/Chess Puzzles", "model": "gpt-5.6-terra", "status": "success", "samples": "10/10"}]
    monkeypatch.setattr(h, "_run", lambda *a, **k: "Eval set ID: inv-abc-123\nSee your eval set log: https://...")
    assert h.submit(Path("/tmp/c.yaml")) == "inv-abc-123"
    monkeypatch.setattr(h, "_run", lambda *a, **k: 'noise\n[{"id": "1", "status": "success"}]')
    assert h.samples("x") == [{"id": "1", "status": "success"}]
