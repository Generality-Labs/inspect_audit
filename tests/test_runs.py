"""Child jobs: one description, run on Hawk or locally, recorded in the sample store."""

import asyncio
import time
from pathlib import Path
from typing import Any

import pytest
from test_helpers.audit import make_audit

from inspect_audit import _audit
from inspect_audit._report import AuditRecord
from inspect_audit.dispatch import (
    COMPLETE,
    FINISHED,
    HawkLogin,
    HawkRuns,
    Job,
    LocalRuns,
    hawk_config,
)

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
        models=["openai/gpt-6-luna"],
        task_args={"scorer": "original"},
        model_roles={"grader": "openai/gpt-6-luna"},
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
    # Hawk receives the full Inspect provider/model identity
    assert [(g.package, [i.name for i in g.items]) for g in config.models or []] == [
        ("inspect-ai", ["openai/gpt-6-luna"])
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
    # the child's own code may read Hawk; Hawk does not tell a runner its API
    assert received["config"].runner.environment == {"HAWK_API_URL": "https://hawk.example"}
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
    runs = LocalRuns(tmp_path / "runs")
    job = Job(
        label="smoke",
        task=f"{tmp_path / 'tiny.py'}@tiny",
        package="unused locally",
        models=["mockllm/model"],
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


def _make_audit(tmp_path: Path, runs: Any) -> _audit.Audit:
    audit = make_audit(
        root=tmp_path,
        runs=runs,
        task="benchmark/task",
        task_package="benchmark",
        investigation_package="inspect_audit",
        investigator_image="image",
        menu=_audit.ModelMenu(
            investigators=["openai/investigator"],
            subjects=["openai/subject"],
            judges=["openai/judge"],
        ),
        supplied=["hawk:supplied-logs"],
    )

    from inspect_ai.util import store_as

    from inspect_audit.dispatch.policy import PolicyStore, ensure_policy

    ensure_policy(audit.authorizer.policy, store_as(PolicyStore), require_existing=False)
    return audit


class FakeRuns:
    def address(self, run_id: str) -> str:
        return f"hawk:{run_id}"

    def __init__(self) -> None:
        self.submitted: list[Job] = []

    async def submit(self, job: Job) -> str:
        self.submitted.append(job)
        return f"run-{len(self.submitted)}"


@pytest.fixture
def record() -> AuditRecord:
    from inspect_ai.util import Store, store_as
    from inspect_ai.util._store import init_subtask_store

    init_subtask_store(Store())
    return store_as(AuditRecord)


def test_launched_jobs_are_recorded_and_only_allowed_models_run(
    tmp_path: Path, record: AuditRecord
) -> None:
    runs = FakeRuns()
    inv = _make_audit(tmp_path, runs)
    benchmark = _audit.run_benchmark(inv)
    out = asyncio.run(benchmark("smoke", ["openai/subject"], ["1"], None, 1, "{}", "{}", "why"))  # type: ignore[arg-type]
    assert "hawk:run-1" in out
    assert record.jobs == [
        {
            "label": "smoke",
            "kind": "benchmark",
            "run_id": "run-1",
            "address": "hawk:run-1",
            "note": "why",
            "policy_sha256": inv.authorizer.policy.digest,
        }
    ]
    from inspect_ai.tool import ToolError

    with pytest.raises(ToolError, match="may not be evaluated"):
        asyncio.run(benchmark("other", ["openai/investigator"], None, None, 1, "{}", "{}", ""))  # type: ignore[arg-type]
    with pytest.raises(ToolError, match="exists"):
        asyncio.run(benchmark("smoke", ["openai/subject"], None, None, 1, "{}", "{}", ""))  # type: ignore[arg-type]


def test_an_investigation_reads_logs_by_job_label_or_supplied_source(
    tmp_path: Path, record: AuditRecord, monkeypatch: pytest.MonkeyPatch
) -> None:
    from inspect_ai.tool import ToolError

    runs = FakeRuns()
    inv = _make_audit(tmp_path, runs)
    record.jobs.append({"label": "smoke", "kind": "benchmark", "run_id": "r", "address": "hawk:r"})
    monkeypatch.setattr(
        _audit,
        "prices_for_models",
        lambda models: {m: {"input": 1.0, "output": 2.0} for m in models},
    )
    investigation = _audit.run_investigation(inv)
    asyncio.run(
        investigation(  # type: ignore[arg-type]
            "a1",
            ["1"],
            ["gold-answer"],
            ["smoke", "hawk:supplied-logs"],
            "openai/investigator",
            "benchmark.task",
            0.5,
            "{}",
            '{"grader": "openai/judge"}',
            "",
        )
    )
    [job] = runs.submitted
    assert job.task == "inspect_audit/investigation" and job.extra_packages == ["benchmark"]
    assert job.task_args["logs"] == ["hawk:r", "hawk:supplied-logs"]
    assert job.task_args["judge"] == {"grader": "openai/judge"}
    assert job.task_args["cost_limit"] == 0.5
    assert job.task_args["source"] == "benchmark.task"
    assert set(job.task_args["model_prices"]) == {
        "openai/investigator",
        "openai/judge",
    }
    with pytest.raises(ToolError, match="neither a job of yours nor a supplied source"):
        asyncio.run(
            investigation(
                "a2",
                ["1"],
                ["gold-answer"],
                ["/etc/passwd"],
                "openai/investigator",
                "b.t",
                0.5,
                "{}",
                "{}",
                "",
            )  # type: ignore[arg-type]
        )


@pytest.mark.parametrize(
    ("model", "credential"),
    [
        ("mockllm/model", None),
        ("openrouter/openai/gpt-4o-mini", "OPENROUTER_API_KEY"),
        ("openai/gpt-4o-mini", "OPENAI_API_KEY"),
    ],
)
def test_a_local_job_sees_only_the_environment_it_needs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, model: str, credential: str | None
) -> None:
    """Benchmark code in a local job must not see the operator's other secrets."""
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "operator-secret")
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-capped")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-direct")
    monkeypatch.chdir(tmp_path)
    (tmp_path / "env.py").write_text(
        TASK_FILE.replace(
            "@task\ndef tiny",
            "from inspect_ai.solver import solver\n@solver\ndef no_calls():\n    async def solve(state, generate):\n        return state\n    return solve\n\n@task\ndef tiny",
        )
        .replace("scorer=match(),", "scorer=match(), solver=no_calls(),")
        .replace(
            "    return Task(",
            "    import os, pathlib\n"
            "    pathlib.Path(__file__).with_name('seen.txt').write_text(','.join(sorted(os.environ)))\n"
            "    return Task(",
        )
    )
    runs = LocalRuns(tmp_path / "runs")
    job = Job(label="env", task=f"{tmp_path / 'env.py'}@tiny", package="", models=[model])
    job.environment = {"IA_BENCHMARK_SETTING": "configured"}

    async def run() -> str:
        run_id = await runs.submit(job)
        deadline = time.monotonic() + 120
        while (status := await runs.status(run_id)) not in FINISHED:
            assert time.monotonic() < deadline, await runs.runner_log(run_id)
            await asyncio.sleep(1)
        return status

    assert asyncio.run(run()) == COMPLETE
    seen = set((tmp_path / "seen.txt").read_text().split(","))
    assert ("OPENROUTER_API_KEY" in seen) == (credential == "OPENROUTER_API_KEY")
    assert ("OPENAI_API_KEY" in seen) == (credential == "OPENAI_API_KEY")
    assert "IA_BENCHMARK_SETTING" in seen
    assert "AWS_SECRET_ACCESS_KEY" not in seen


@pytest.mark.parametrize("kind", ["benchmark", "investigation"])
def test_task_arguments_cannot_place_code_or_name_other_models(kind: str) -> None:
    from inspect_ai.tool import ToolError

    from inspect_audit.dispatch.policy import (
        AuditAuthorizer,
        LaunchPolicy,
        Models,
        canonical,
    )

    # the operator's own sandbox, image and path choices stand (SWE-bench needs them);
    # running outside a sandbox and judges outside the policy do not
    for args, allowed in [
        (
            {
                "judge_prompt": "original",
                "grader_model": "openai/judge",
                "judges": ["openai/judge"],
            },
            True,
        ),
        (
            {
                "sandbox_type": "k8s",
                "image_name_template": "sweb.{issue}:latest",
                "sandbox_config": None,
            },
            True,
        ),
        ({"items": [{"docker_image": "theirs:1"}], "data_dir": "/data"}, True),
        ({"sandbox": "local"}, False),
        ({"sandbox_type": "LOCAL"}, False),
        ({"nested": {"data_dir": "/etc", "judge": "openai/other"}}, False),
        ({"grader_model": "openai/other"}, False),
        ({"judges": ["openai/other"]}, False),
        ({"models": {"reviewer": "openai/other"}}, False),
    ]:
        policy = LaunchPolicy(
            task="bench",
            task_package="bench",
            investigation_package="investigation",
            investigator_image="img",
            models=Models(
                subjects=("subject",), investigators=("investigator",), judges=("openai/judge",)
            ),
            checks=("gold-answer",),
            task_args_json=canonical(args),
            configuration_json="{}",
        )
        job = Job(label="probe", task="bench", package="bench", models=["subject"], task_args=args)
        if kind == "investigation":
            job = Job(
                label="probe",
                task="inspect_audit/investigation",
                package="investigation",
                models=["investigator"],
                extra_packages=["bench"],
                task_args={
                    "task": "bench",
                    "task_args": args,
                    "source": "bench",
                    "investigator_image": "img",
                    "checks": ["gold-answer"],
                    "samples": ["1"],
                    "cost_limit": 0.1,
                },
            )
        authorizer = AuditAuthorizer(policy)
        if allowed:
            assert authorizer.authorise(job, kind, set()).task_args == job.task_args
        else:
            with pytest.raises(ToolError, match="task_args"):
                authorizer.authorise(job, kind, set())


def test_the_runner_credentials_come_from_hawks_real_hook(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Hawk's own RefreshTokenHook, as installed, not a copy of its behaviour.

    2026-10-20: a fake modelled on an older hook passed while hawk 3.7's hook declined
    the ask and the budget tool got no token on a runner. Only the network refresh
    is replaced; the hook's own rules about which asks it answers run as shipped.
    """
    from hawk.runner import refresh_token as hawk_hook
    from inspect_ai.hooks import _hooks

    refreshes: list[str] = []

    def refresh(token: str, url: str, client: str) -> tuple[Any, str]:
        refreshes.append(token)
        rotated = f"refresh-{len(refreshes)}"
        return (
            hawk_hook._TokenRefreshResponse(  # pyright: ignore[reportPrivateUsage]
                access_token=f"access-{len(refreshes)}", expires_in=3600, refresh_token=rotated
            ),
            token,
        )

    monkeypatch.setattr(hawk_hook, "_do_token_refresh_with_recovery", refresh)
    # what a runner's environment holds: the gateway's address and its own credential
    monkeypatch.setenv("AI_GATEWAY_BASE_URL", "https://middleman.example")
    monkeypatch.setenv("BASE_API_KEY", "startup-jwt")
    hook = hawk_hook.refresh_token_hook(
        "https://idp.example/token", "client", "refresh-0", validate=False
    )()
    monkeypatch.setattr(_hooks, "get_all_hooks", lambda: [hook])

    login = HawkLogin("https://hawk.example")
    assert asyncio.run(login.access_token()) == "access-1"
    # the child job gets the refresh token as rotated by that refresh
    assert asyncio.run(login.refresh_token()) == "refresh-1"


def test_a_dataset_without_ids_is_selected_by_position(tmp_path: Path) -> None:
    """Inspect matches sample ids before it numbers id-less samples, so a positional
    range is the only selection that works for them (2026-10-20: sample_ids=["1"] on
    SimpleQA Verified failed at task load on Hawk)."""
    from hawk.core.types import EvalSetConfig

    job = Job(label="r", task="b/t", package="b", models=["m/x"], sample_range=(3, 5))
    assert EvalSetConfig.model_validate(hawk_config(job)).limit == (2, 5)

    (tmp_path / "noids.py").write_text(
        TASK_FILE.replace(", id=str(i)) for i in range(1, 4)", ") for i in range(1, 4)").replace(
            "Sample(input=greeting", "Sample(input=f'{greeting} {i}'"
        )
    )
    runs = LocalRuns(tmp_path / "runs")
    job = Job(
        label="pos",
        task=f"{tmp_path / 'noids.py'}@tiny",
        package="",
        models=["mockllm/model"],
        sample_range=(2, 3),
    )

    async def run() -> list[Path]:
        run_id = await runs.submit(job)
        deadline = time.monotonic() + 120
        while await runs.status(run_id) not in FINISHED:
            assert time.monotonic() < deadline, await runs.runner_log(run_id)
            await asyncio.sleep(1)
        return await runs.logs(run_id, tmp_path / "out")

    from inspect_ai.log import read_eval_log

    [log] = [read_eval_log(str(p)) for p in asyncio.run(run())]
    assert [s.input for s in log.samples or []] == ["hello 2", "hello 3"]


def test_a_local_job_receives_string_arguments_exactly(tmp_path: Path) -> None:
    """Inspect parses each -T value as YAML and splits on commas; Hawk passes JSON.
    The two backends must give the task the same arguments."""
    (tmp_path / "tiny.py").write_text(TASK_FILE)
    runs = LocalRuns(tmp_path / "runs")
    job = Job(
        label="args",
        task=f"{tmp_path / 'tiny.py'}@tiny",
        package="",
        models=["mockllm/model"],
        task_args={"greeting": "a, yes"},
        sample_ids=["1"],
    )

    async def run() -> list[Path]:
        run_id = await runs.submit(job)
        deadline = time.monotonic() + 120
        while await runs.status(run_id) not in FINISHED:
            assert time.monotonic() < deadline, await runs.runner_log(run_id)
            await asyncio.sleep(1)
        return await runs.logs(run_id, tmp_path / "out")

    from inspect_ai.log import read_eval_log

    [log] = [read_eval_log(str(p)) for p in asyncio.run(run())]
    assert log.eval.task_args == {"greeting": "a, yes"}


def test_hawk_logs_are_fetched_with_the_runners_own_credentials(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A child investigation fetching hawk: logs on a runner uses Hawk's real hook."""
    import hawk.cli.download
    import hawk.client
    from hawk.runner import refresh_token as hawk_hook
    from inspect_ai.hooks import _hooks

    from inspect_audit._log_sources import fetch_logs

    monkeypatch.setattr(
        hawk_hook,
        "_do_token_refresh_with_recovery",
        lambda token, url, client: (
            hawk_hook._TokenRefreshResponse(access_token="runner-access", expires_in=3600),  # pyright: ignore[reportPrivateUsage]
            token,
        ),
    )
    monkeypatch.setenv("AI_GATEWAY_BASE_URL", "https://middleman.example")
    monkeypatch.setenv("BASE_API_KEY", "startup-jwt")
    monkeypatch.setenv("HAWK_API_URL", "https://hawk.example")
    monkeypatch.delenv("HAWK_ACCESS_TOKEN", raising=False)
    hook = hawk_hook.refresh_token_hook("https://idp", "client", "rt", validate=False)()
    monkeypatch.setattr(_hooks, "get_all_hooks", lambda: [hook])
    used: list[str] = []

    class Client:
        def __init__(self, token: str, api_url: str | None = None) -> None:
            used.append(token)

        async def __aenter__(self) -> "Client":
            return self

        async def __aexit__(self, *args: Any) -> None:
            pass

        async def get_log_files(self, eval_set_id: str) -> list[dict[str, str]]:
            return [{"name": f"{eval_set_id}/a.eval"}]

        async def get_download_urls(self, names: list[str]) -> Any:
            yield "https://download/a", "a.eval"

    async def download_file(url: str, dest: Path) -> None:
        dest.write_bytes(b"log")

    monkeypatch.setattr(hawk.client, "HawkClient", Client)
    monkeypatch.setattr(hawk.cli.download, "download_file", download_file)
    monkeypatch.setattr(hawk.cli.download, "_assert_server_supports_batch_download", lambda: None)
    assert (Path(fetch_logs("hawk:run")) / "run" / "a.eval").read_bytes() == b"log"
    assert used == ["runner-access"]


def test_an_investigation_reads_a_log_directory(tmp_path: Path) -> None:
    """A local job's address is its log directory; operator sources may be URLs."""
    from test_helpers.logs import run_fixture_eval

    from inspect_audit._log_sources import log_files

    log = Path(run_fixture_eval(str(tmp_path / "logs")))
    assert log_files([str(log.parent)]) == [str(log)]
    with pytest.raises(ValueError, match="holds no eval logs"):
        log_files([str(tmp_path / "empty")])


def test_a_balance_without_a_number_is_unknown_not_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    """$0.00 would tell the agent to stop; an answer it cannot read must say so."""
    import json as jsonlib
    import urllib.request

    class Response:
        def __init__(self, body: dict[str, Any]) -> None:
            self.body = jsonlib.dumps(body).encode()

        def __enter__(self) -> "Response":
            return self

        def __exit__(self, *args: Any) -> None:
            pass

        def read(self, *args: Any) -> bytes:
            return self.body

    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
    monkeypatch.setattr(
        urllib.request, "urlopen", lambda request, timeout=None: Response({"data": {"limit": 5}})
    )
    [line] = asyncio.run(_audit.key_balance(None, openrouter=True))
    assert "unknown" in line and "$0.00" not in line


def test_an_empty_judge_reaches_the_investigation_as_empty(
    tmp_path: Path, record: AuditRecord, monkeypatch: pytest.MonkeyPatch
) -> None:
    """{} means "their grader calls no model"; None means "infer it from the logs"."""
    runs = FakeRuns()
    inv = _make_audit(tmp_path, runs)
    monkeypatch.setattr(_audit, "prices_for_models", lambda models: {m: {} for m in models})
    asyncio.run(
        _audit.run_investigation(inv)(  # type: ignore[arg-type]
            "a", ["1"], ["gold-answer"], [], "openai/investigator", "b.t", 0.5, "{}", "{}", ""
        )
    )
    assert runs.submitted[0].task_args["judge"] == {}


def test_local_completion_reads_only_the_output_tail(tmp_path: Path, monkeypatch) -> None:
    import json

    runs = LocalRuns(tmp_path / "runs")
    directory = runs.root / "child"
    directory.mkdir()
    output = tmp_path / "output.txt"
    output.write_text("x" * 100_000 + "\n" + json.dumps({"event": "done", "success": True}) + "\n")
    (directory / "launch.json").write_text(json.dumps({"output_file": str(output)}))
    read_text = Path.read_text

    def read_metadata(path, *args, **kwargs):
        assert path != output, "runner output must not be loaded in full"
        return read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", read_metadata)
    assert asyncio.run(runs.status("child")) == COMPLETE


def test_even_an_empty_configuration_requires_a_bound_policy(
    tmp_path: Path, record: AuditRecord
) -> None:
    from inspect_ai.util import Store
    from inspect_ai.util._store import init_subtask_store

    runs = FakeRuns()
    audit = _make_audit(tmp_path, runs)
    init_subtask_store(Store())
    job = Job(label="child", task="benchmark/task", package="benchmark", models=["openai/subject"])
    with pytest.raises(RuntimeError, match="differs from its saved launch policy"):
        asyncio.run(audit.launch(job, "benchmark", "unbound"))
    assert not runs.submitted
