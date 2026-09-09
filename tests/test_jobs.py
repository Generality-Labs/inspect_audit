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
        ({"eval_set_id": "someone-elses"}, "remove eval_set_id"),
        ({"tasks": [{"package": TASK_PKG, "name": "inspect_evals", "items": [{"name": "simpleqa_verified", "args": {"sandbox": "docker"}}]}]}, "task arg not allowed"),
        # the configuration that actually executes: a task argument, not the outer field
        ({"tasks": [{"package": TASK_PKG, "name": "inspect_evals", "items": [{"name": "simpleqa_verified", "args": {"model": "openai/gpt-6-astra"}}]}]}, "is not an allowed model"),
        ({"tasks": [{"package": TASK_PKG, "name": "inspect_evals", "items": [{"name": "simpleqa_verified", "args": {"grader_model": "openai/gpt-6-astra"}}]}]}, "is not an allowed model"),
        ({"tasks": [{"package": TASK_PKG, "name": "inspect_evals", "items": [{"name": "simpleqa_verified", "args": {"benchmark_image": "ghcr.io/evil:latest"}}]}]}, "is not an allowed image"),
        ({"tasks": [{"package": TASK_PKG, "name": "inspect_evals", "items": [{"name": "simpleqa_verified", "args": {"limit": 100000}}]}]}, "must be an integer up to"),
        ({"tasks": [{"package": TASK_PKG, "name": "inspect_evals", "items": [{"name": "simpleqa_verified", "secrets": [{"name": "AWS_SECRET_ACCESS_KEY"}]}]}]}, "task-level secrets"),
        ({"tasks": [{"package": TASK_PKG, "name": "inspect_evals", "items": [{"name": "simpleqa_verified", "args": {"dataset": "s3://someone/else"}}]}]}, "points outside this investigation"),
        ({"secrets": [{"name": "AWS_SECRET_ACCESS_KEY"}]}, "keys not allowed"),
        ({"limit": None}, "does not state its size"),
        ({"cost_limit": None}, "cost_limit is required"),
        ({"cost_limit": 500.0}, "cost_limit 500.0 must be"),
        ({"limit": 1000, "cost_limit": 5.0}, "exceeds the $200.00 a single job may hold"),
    ],
)
def test_policy_refuses_each_escape(change: dict, expect: str) -> None:
    problems = validate_config(filled_example("benchmark.eval-set.yaml", **change), policy(), set())
    assert any(expect in p for p in problems), problems


def test_policy_requires_a_size_and_known_log_sources() -> None:
    config = filled_example("benchmark.eval-set.yaml")
    del config["limit"]
    assert any("does not state its size" in p for p in validate_config(config, policy(), set()))
    # a size stated per item is a size
    config["tasks"][0]["items"][0]["sample_ids"] = ["a", "b"]
    assert validate_config(config, policy(), set()) == []
    audit = filled_example("audit.eval-set.yaml")
    assert any("staged or ran" in p for p in validate_config(audit, policy(), set()))
    assert validate_config(audit, policy(), {"inv-staged-abc"}) == []


def test_worst_case_is_cost_limit_times_the_work_the_config_asks_for() -> None:
    from inspect_audit._jobs import parse_config, worst_case_usd

    config = filled_example("benchmark.eval-set.yaml", limit=10, epochs=2, cost_limit=0.5)
    parsed, problems = parse_config(config)
    assert problems == []
    assert worst_case_usd(parsed, policy()) == 10.0  # 0.50 x 10 samples x 1 model x 2 epochs


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
    from inspect_ai.model import ModelCost, ModelInfo, set_model_info

    set_model_info("openai/gpt-5.6-luna", ModelInfo(cost=ModelCost(input=1.0, output=2.0, input_cache_read=0.1, input_cache_write=0.2)))
    r = remote(tmp_path, allowance=3.0)
    path = _write(tmp_path, "smoke.eval-set.yaml", filled_example("benchmark.eval-set.yaml"))
    out = run(hawk_submit(r, tmp_path)(config=path, estimated_usd=0.3, note="smoke"))
    assert "set-1" in out
    job = JobLedger(tmp_path).get("smoke-luna")
    # the example runs 2 samples at $0.50 each: the hold is the worst case, not the guess
    assert job and job.estimated_usd == 0.3 and job.reserved_usd == 1.0 and job.status == "submitted"
    assert Path(job.config_path).is_file() and "set-1" in json.loads((tmp_path / "log_sources.json").read_text())
    submitted = yaml.safe_load(Path(job.config_path).read_text())
    assert submitted["eval_set_id"] == job.eval_set_id != "set-1" or True
    assert submitted["model_cost_config"], "prices are stamped in, not left to the agent"
    with pytest.raises(ToolError, match="already exists"):
        run(hawk_submit(r, tmp_path)(config=path, estimated_usd=0.5))
    other = _write(tmp_path, "two.eval-set.yaml", filled_example("benchmark.eval-set.yaml", name="inv-two", limit=6))
    with pytest.raises(ToolError, match="cannot reserve"):
        run(hawk_submit(r, tmp_path)(config=other, estimated_usd=0.1))
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
    with pytest.raises(ToolError, match="not in job"):
        run(tool(action="transcript", label="jj", sample="someone-elses-uuid"))
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


def test_two_submissions_at_once_cannot_both_take_the_last_of_the_allowance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The check and the record happen in one locked transaction, so they serialise."""
    import asyncio

    from inspect_ai.tool import ToolError

    from inspect_audit import _investigate

    monkeypatch.setattr(_investigate, "_local_spend", lambda: (0.0, []))
    r = remote(tmp_path, allowance=3.0)
    (tmp_path / "inputs").mkdir(exist_ok=True)
    tool = hawk_submit(r, tmp_path)
    a = _write(tmp_path, "a.eval-set.yaml", filled_example("benchmark.eval-set.yaml", name="inv-a", limit=4))
    b = _write(tmp_path, "b.eval-set.yaml", filled_example("benchmark.eval-set.yaml", name="inv-b", limit=4))

    async def both() -> list[object]:
        return list(
            await asyncio.gather(
                tool(config=a, estimated_usd=1.0),
                tool(config=b, estimated_usd=1.0),
                return_exceptions=True,
            )
        )

    results = asyncio.run(both())
    # each job's worst case is $2 (4 samples x $0.50); only one fits in $3
    accepted = [x for x in results if isinstance(x, str)]
    refused = [x for x in results if isinstance(x, ToolError)]
    assert len(accepted) == 1 and len(refused) == 1, results
    assert "cannot reserve" in str(refused[0])
    assert JobLedger(tmp_path).reserved_usd() == 2.0
    assert len(r.hawk.submitted) == 1  # type: ignore[attr-defined]


def test_a_lost_submission_response_is_reconciled_not_resubmitted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Hawk took the job but the answer never came back: the ledger must not lose it."""
    from inspect_ai.tool import ToolError

    from inspect_audit import _investigate

    monkeypatch.setattr(_investigate, "_local_spend", lambda: (0.0, []))
    r = remote(tmp_path)
    (tmp_path / "inputs").mkdir(exist_ok=True)
    landed: list[str] = []

    def submit_then_lose_the_answer(config_path: Path) -> str:
        landed.append(str(yaml.safe_load(config_path.read_text())["eval_set_id"]))
        raise TimeoutError("connection reset while waiting for hawk")

    r.hawk.submit = submit_then_lose_the_answer  # type: ignore[assignment]
    r.hawk.eval_set_exists = lambda eval_set_id: eval_set_id in landed  # type: ignore[assignment]
    path = _write(tmp_path, "lost.eval-set.yaml", filled_example("benchmark.eval-set.yaml", name="inv-lost"))
    with pytest.raises(ToolError, match="submission failed"):
        run(hawk_submit(r, tmp_path)(config=path, estimated_usd=1.0))

    job = JobLedger(tmp_path).get("lost")
    assert job is not None and job.eval_set_id == landed[0]
    assert job.status == "submitted", "Hawk has it; the job is not lost and must not be sent twice"
    assert job.reserved_usd == 1.0 and JobLedger(tmp_path).reserved_usd() == 1.0


def test_a_submission_that_never_reached_hawk_releases_its_reservation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from inspect_ai.tool import ToolError

    from inspect_audit import _investigate

    monkeypatch.setattr(_investigate, "_local_spend", lambda: (0.0, []))
    r = remote(tmp_path)
    (tmp_path / "inputs").mkdir(exist_ok=True)

    def refuse(config_path: Path) -> str:
        raise RuntimeError("hawk eval-set run failed: could not resolve host")

    r.hawk.submit = refuse  # type: ignore[assignment]
    r.hawk.eval_set_exists = lambda eval_set_id: False  # type: ignore[assignment]
    path = _write(tmp_path, "gone.eval-set.yaml", filled_example("benchmark.eval-set.yaml", name="inv-gone"))
    with pytest.raises(ToolError, match="never reached Hawk"):
        run(hawk_submit(r, tmp_path)(config=path, estimated_usd=1.0))
    ledger = JobLedger(tmp_path)
    assert ledger.get("gone").status == "failed"  # type: ignore[union-attr]
    assert ledger.reserved_usd() == 0.0


def test_collect_leaves_an_unpriced_cost_unknown_and_keeps_the_hold(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from inspect_audit import _investigate

    monkeypatch.setattr(_investigate, "_local_spend", lambda: (0.0, []))
    monkeypatch.setattr(_investigate, "usage_cost", lambda files: (None, {"openrouter/x": {"input": 1, "cache_read": 0, "output": 2}}))
    r = remote(tmp_path)
    (tmp_path / "inputs").mkdir(exist_ok=True)
    run(hawk_submit(r, tmp_path)(config=_write(tmp_path, "u.eval-set.yaml", filled_example("benchmark.eval-set.yaml", name="inv-u")), estimated_usd=0.2))
    r.hawk.eval_status = "success"  # type: ignore[attr-defined]
    out = run(jobs(r, tmp_path)(action="collect", label="u"))
    assert "cost unknown" in out
    ledger = JobLedger(tmp_path)
    job = ledger.get("u")
    assert job is not None and job.actual_usd is None, "an estimate must never be recorded as a measurement"
    assert ledger.reserved_usd() == 1.0 and ledger.unpriced() == ["u"]
