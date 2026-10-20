"""Child jobs: one description, run on Hawk or locally, recorded in the sample store."""

import asyncio
import time
from pathlib import Path
from typing import Any

import pytest

from inspect_audit import _investigate
from inspect_audit._report import InvestigationRecord
from inspect_audit._runs import COMPLETE, FINISHED, HawkLogin, HawkRuns, Job, LocalRuns, hawk_config

TASK_FILE = """
from inspect_ai import Task, task
from inspect_ai.dataset import Sample
from inspect_ai.scorer import match


@task
def tiny(greeting: str = "hello"):
    return Task(
        dataset=[Sample(input=greeting, target="x", id=str(i)) for i in range(1, 4)],
        scorer=match(),
    )
"""


def test_a_job_renders_as_a_config_hawk_accepts() -> None:
    from hawk.core.types import EvalSetConfig

    job = Job(
        label="smoke",
        task="inspect_evals/simpleqa_verified",
        package="inspect-evals@git+https://example.org/evals@abc",
        models=["openai/gpt-5-mini"],
        task_args={"scorer": "original"},
        model_roles={"grader": "openai/gpt-5-mini"},
        sample_ids=["1", "2"],
        epochs=2,
    )
    config = EvalSetConfig.model_validate(hawk_config(job))
    [task] = config.tasks
    assert task.name == "inspect_evals"
    [item] = task.items
    assert (item.name, item.args, item.sample_ids) == (
        "simpleqa_verified",
        {"scorer": "original"},
        ["1", "2"],
    )
    # models go through Hawk's proxy, under its openrouter group
    assert [(g.name, [i.name for i in g.items]) for g in config.models or []] == [
        ("openrouter", ["openai/gpt-5-mini"])
    ]
    assert config.retry_attempts == 0


def test_a_child_job_runs_as_this_runs_user_with_its_current_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """On a runner the refresh credential rotates when the access token refreshes.

    The child must get the rotated one, so the access token is fetched first; and
    it must be this run's own, so the child's calls bill to the same key.
    """
    import hawk.client
    from inspect_ai.hooks import _hooks

    received: dict[str, Any] = {}

    class Hook:
        _current_refresh_token = "old"

    Hook.__module__ = "hawk.runner.refresh_token"
    hook = Hook()

    def refresh(name: str, value: str) -> str:
        # hawk >= 3.7 answers only an ask it can tell is gateway traffic
        if value != "gateway-marker":
            return ""
        hook._current_refresh_token = "rotated"
        return "current-access"

    class Client:
        def __init__(self, **kwargs: Any) -> None:
            received.update(kwargs)

        async def __aenter__(self) -> "Client":
            return self

        async def __aexit__(self, *args: Any) -> None:
            pass

        async def create_eval_set(self, config: Any, **kwargs: Any) -> dict[str, Any]:
            received.update(kwargs)
            received["config"] = config
            # hawk >= 3.5 returns the API body rather than the bare id
            return {"eval_set_id": "child-job", "warnings": []}

    monkeypatch.setenv("BASE_API_KEY", "gateway-marker")
    monkeypatch.setattr(_hooks, "override_api_key", refresh)
    monkeypatch.setattr(_hooks, "get_all_hooks", lambda: [hook])
    monkeypatch.setattr(hawk.client, "HawkClient", Client)
    job = Job(label="child", task="benchmark/task", package="benchmark", models=["m/x"])
    assert asyncio.run(HawkRuns("https://hawk.example").submit(job)) == "child-job"
    assert received["token"] == "current-access"
    assert received["refresh_token"] == "rotated"
    assert "secrets" not in received, "no provider key travels with a child job"


def test_a_laptop_asks_the_operators_own_hawk_past_the_venvs(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Only the `hawk` that wrote the keychain entry reads it without a prompt."""
    import sys

    venv = tmp_path / "venv"
    own = tmp_path / "tools"
    for directory in (venv, own):
        directory.mkdir()
        (directory / "hawk").write_text("")
        (directory / "hawk").chmod(0o755)
    (venv / "python").write_text("")
    monkeypatch.setattr(sys, "executable", str(venv / "python"))
    monkeypatch.setenv("PATH", f"{venv}:{own}")
    assert HawkLogin("https://hawk.example").binary == str(own / "hawk")


def test_a_local_job_runs_detached_and_hands_back_its_logs(tmp_path: Path) -> None:
    """A real `inspect eval-set` process, on mockllm: submitted, finished, collected."""
    (tmp_path / "tiny.py").write_text(TASK_FILE)
    runs = LocalRuns(tmp_path / "runs", provider="mockllm")
    job = Job(
        label="smoke",
        task=f"{tmp_path / 'tiny.py'}@tiny",
        package="unused locally",
        models=["model"],
        task_args={"greeting": "hi there"},
        sample_ids=["1", "2"],
    )

    async def run() -> tuple[str, list[Path]]:
        run_id = await runs.submit(job)
        deadline = time.monotonic() + 120
        while (status := await runs.status(run_id)) not in FINISHED:
            assert time.monotonic() < deadline, await runs.runner_log(run_id)
            await asyncio.sleep(1)
        return status, await runs.logs(run_id, tmp_path / "out")

    status, logs = asyncio.run(run())
    assert status == COMPLETE
    from inspect_ai.log import read_eval_log

    [log] = [read_eval_log(str(p)) for p in logs]
    assert log.eval.task_args == {"greeting": "hi there"}
    assert sorted(str(s.id) for s in log.samples or []) == ["1", "2"]


def _investigation(tmp_path: Path, runs: Any) -> _investigate.Investigation:
    return _investigate.Investigation(
        root=tmp_path,
        runs=runs,
        where="hawk",
        task="benchmark/task",
        task_package="benchmark",
        audit_package="inspect_audit",
        auditor_image="image",
        menu=_investigate.ModelMenu(
            auditors=["openai/auditor"], subjects=["openai/subject"], judges=["openai/judge"]
        ),
        supplied=["hawk:supplied-logs"],
    )


class FakeRuns:
    def __init__(self) -> None:
        self.submitted: list[Job] = []

    async def submit(self, job: Job) -> str:
        self.submitted.append(job)
        return f"run-{len(self.submitted)}"


@pytest.fixture
def record(monkeypatch: pytest.MonkeyPatch) -> InvestigationRecord:
    from inspect_ai.util import Store

    # its own store: a bare StoreModel binds to a shared one, which leaks between tests
    held = InvestigationRecord(store=Store())
    monkeypatch.setattr(_investigate, "store_as", lambda _: held)
    return held


def test_launched_jobs_are_recorded_and_only_allowed_models_run(
    tmp_path: Path, record: InvestigationRecord
) -> None:
    runs = FakeRuns()
    inv = _investigation(tmp_path, runs)
    benchmark = _investigate.run_benchmark(inv)
    out = asyncio.run(benchmark("smoke", ["openai/subject"], ["1"], 1, "{}", "{}", "why"))  # type: ignore[arg-type]
    assert "hawk:run-1" in out
    assert record.jobs == [
        {
            "label": "smoke",
            "kind": "benchmark",
            "run_id": "run-1",
            "address": "hawk:run-1",
            "note": "why",
        }
    ]
    from inspect_ai.tool import ToolError

    with pytest.raises(ToolError, match="may not be evaluated"):
        asyncio.run(benchmark("other", ["openai/auditor"], None, 1, "{}", "{}", ""))  # type: ignore[arg-type]
    with pytest.raises(ToolError, match="exists"):
        asyncio.run(benchmark("smoke", ["openai/subject"], None, 1, "{}", "{}", ""))  # type: ignore[arg-type]


def test_an_audit_reads_logs_by_job_label_or_supplied_source(
    tmp_path: Path, record: InvestigationRecord, monkeypatch: pytest.MonkeyPatch
) -> None:
    from inspect_ai.tool import ToolError

    runs = FakeRuns()
    inv = _investigation(tmp_path, runs)
    record.jobs.append({"label": "smoke", "kind": "benchmark", "run_id": "r", "address": "hawk:r"})
    monkeypatch.setattr(
        _investigate,
        "openrouter_prices",
        lambda models: {f"openrouter/{m}": {"input": 1.0, "output": 2.0} for m in models},
    )
    audit = _investigate.run_audit(inv)
    asyncio.run(
        audit(  # type: ignore[arg-type]
            "a1",
            ["1"],
            ["gold-answer"],
            ["smoke", "hawk:supplied-logs"],
            "openai/auditor",
            0.5,
            "{}",
            '{"grader": "openai/judge"}',
            "",
        )
    )
    [job] = runs.submitted
    assert job.task == "inspect_audit/audit" and job.extra_packages == ["benchmark"]
    assert job.task_args["logs"] == ["hawk:r", "hawk:supplied-logs"]
    assert job.task_args["judge"] == {"grader": "openrouter/openai/judge"}
    assert job.task_args["cost_limit"] == 0.5
    assert set(job.task_args["model_prices"]) == {
        "openrouter/openai/auditor",
        "openrouter/openai/judge",
    }
    with pytest.raises(ToolError, match="neither a job of yours nor a supplied source"):
        asyncio.run(
            audit(
                "a2", ["1"], ["gold-answer"], ["/etc/passwd"], "openai/auditor", 0.5, "{}", "{}", ""
            )  # type: ignore[arg-type]
        )


def test_a_local_job_sees_only_the_environment_it_needs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Benchmark code in a local job must not see the operator's other secrets."""
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "operator-secret")
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-capped")
    (tmp_path / "env.py").write_text(
        TASK_FILE.replace(
            "    return Task(",
            "    import os, pathlib\n"
            "    pathlib.Path(__file__).with_name('seen.txt').write_text(','.join(sorted(os.environ)))\n"
            "    return Task(",
        )
    )
    runs = LocalRuns(tmp_path / "runs", provider="mockllm")
    job = Job(label="env", task=f"{tmp_path / 'env.py'}@tiny", package="", models=["model"])

    async def run() -> str:
        run_id = await runs.submit(job)
        deadline = time.monotonic() + 120
        while (status := await runs.status(run_id)) not in FINISHED:
            assert time.monotonic() < deadline, await runs.runner_log(run_id)
            await asyncio.sleep(1)
        return status

    assert asyncio.run(run()) == COMPLETE
    seen = set((tmp_path / "seen.txt").read_text().split(","))
    assert "OPENROUTER_API_KEY" in seen
    assert "AWS_SECRET_ACCESS_KEY" not in seen


def test_task_arguments_cannot_place_code_or_name_other_models() -> None:
    allowed = ["openai/judge"]
    assert _investigate.argument_problems({"scorer": "original", "profile": "x"}, allowed) == []
    assert (
        _investigate.argument_problems({"grader_model": "openrouter/openai/judge"}, allowed) == []
    )
    problems = _investigate.argument_problems(
        {
            "sandbox": "local",
            "nested": {"data_dir": "/etc", "judge": "openai/gpt-6-astra"},
            "items": [{"docker_image": "evil:latest"}],
        },
        allowed,
    )
    assert [p.split(":")[0] for p in problems] == [
        "task_args.sandbox",
        "task_args.nested.data_dir",
        "task_args.nested.judge",
        "task_args.items[0].docker_image",
    ]
