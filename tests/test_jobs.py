"""Remote jobs: the policy over agent-written configs, staging, submission, ledger, collection."""

import asyncio
import json
from pathlib import Path

import pytest
import yaml

from inspect_audit import _jobs
from inspect_audit._investigate import Remote, hawk_submit, jobs, stage_logs
from inspect_audit._jobs import JobLedger, Policy, task_package_name, validate_config

TASK_PKG = "git+https://github.com/UKGovernmentBEIS/inspect_evals@abc"
AUDIT_PKG = "git+https://github.com/Generality-Labs/inspect_audit@def"
IMAGE = "ghcr.io/x/auditor@sha256:0"
HAWK = "https://hawk.example"
EXAMPLES = Path(__file__).parent.parent / "src/inspect_audit/investigation/skills/investigating/examples"


class FakeHawk:
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
        .replace("<hawk:... from stage_logs or hawk:<eval set id of your job>>", "hawk:inv-staged-abc/inputs/logs")
        .replace("<from stage_logs, when auditing the supplied logs>   # otherwise omit", "inv-staged-abc")
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


def test_stage_logs_then_audit_over_them(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from inspect_audit import _investigate

    monkeypatch.setattr(_investigate, "_local_spend", lambda: (0.0, []))
    staged: list[str] = []

    def fake_stage(local_dir, bucket, eval_set_id, profile):  # noqa: ANN001, ANN202
        staged.append(eval_set_id)
        return f"hawk:{eval_set_id}/inputs/logs"

    monkeypatch.setattr(_investigate, "stage_logs_to_s3", fake_stage)
    (tmp_path / "inputs" / "logs").mkdir(parents=True)
    (tmp_path / "inputs" / "logs" / "a.eval").write_bytes(b"x")
    r = remote(tmp_path)
    out = run(stage_logs(r, tmp_path)(label="sqav"))
    eval_set_id = staged[0]
    assert eval_set_id.startswith("inv-sqav-") and eval_set_id in out
    config = filled_example("audit.eval-set.yaml", eval_set_id=eval_set_id)
    config["tasks"][0]["items"][0]["args"]["logs"] = f"hawk:{eval_set_id}/inputs/logs"
    path = _write(tmp_path, "audit.eval-set.yaml", config)
    assert "set-1" in run(hawk_submit(r, tmp_path)(config=path, estimated_usd=1.0))
    # a source this investigation did not create is refused
    foreign = filled_example("audit.eval-set.yaml", name="inv-foreign")
    del foreign["eval_set_id"]
    foreign["tasks"][0]["items"][0]["args"]["logs"] = "hawk:someone-elses-set"
    from inspect_ai.tool import ToolError

    with pytest.raises(ToolError, match="staged or ran"):
        run(hawk_submit(r, tmp_path)(config=_write(tmp_path, "f.eval-set.yaml", foreign), estimated_usd=1.0))


def test_jobs_status_wait_collect_release_reservation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from inspect_ai.tool import ToolError

    from inspect_audit import _investigate

    monkeypatch.setattr(_investigate, "_local_spend", lambda: (0.0, []))
    monkeypatch.setattr(_investigate, "usage_cost", lambda files: (0.42, {"openrouter/m": {"input": 1, "cache_read": 2, "output": 3}}))
    r = remote(tmp_path)
    (tmp_path / "inputs").mkdir()
    run(hawk_submit(r, tmp_path)(config=_write(tmp_path, "j.eval-set.yaml", filled_example("benchmark.eval-set.yaml", name="inv-jj")), estimated_usd=3))
    tool = jobs(r, tmp_path)
    assert "running" in run(tool(action="status", label="jj"))
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
    assert "jj (eval-set)" in run(tool(action="list"))
    run(tool(action="stop", label="jj"))
    assert r.hawk.stopped == ["set-1"]  # type: ignore[attr-defined]


def test_hawk_cli_parsing(monkeypatch: pytest.MonkeyPatch) -> None:
    h = _jobs.Hawk("https://h", None)
    table = "Eval Set: x\n\nTask   Model   Status   Samples\n----  ----  ----  ----\naudit/bench/Chess Puzzles  gpt-5.6-terra  success   10/10\n"
    monkeypatch.setattr(h, "_run", lambda *a, **k: table)
    assert h.evals("x") == [{"task": "audit/bench/Chess Puzzles", "model": "gpt-5.6-terra", "status": "success", "samples": "10/10"}]
    monkeypatch.setattr(h, "_run", lambda *a, **k: "Eval set ID: inv-abc-123\nSee your eval set log: https://...")
    assert h.submit(Path("/tmp/c.yaml")) == "inv-abc-123"
    monkeypatch.setattr(h, "_run", lambda *a, **k: 'noise\n[{"id": "1", "status": "success"}]')
    assert h.samples("x") == [{"id": "1", "status": "success"}]
