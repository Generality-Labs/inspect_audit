"""Child jobs: one description, run on Hawk or locally, recorded in the sample store."""

import asyncio
import time
from pathlib import Path
from typing import Any

import pytest
from test_helpers.audit import make_audit

from inspect_audit import _audit, _prices
from inspect_audit._report import AuditRecord
from inspect_audit.dispatch import (
    FINISHED,
    HawkLogin,
    HawkRuns,
    Job,
    JobState,
    LocalRuns,
    hawk_config,
)
from inspect_audit.dispatch.policy import Models

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


def test_an_investigations_judges_get_credentials_in_either_mode() -> None:
    """The benchmark's judge is built when the investigation loads; Hawk gives a job
    credentials only for the models its config declares (smoke round 7: "No
    OPENROUTER_API_KEY defined", FRICTION 94)."""
    from hawk.core.types import EvalSetConfig

    job = Job(
        label="inv",
        task="inspect_audit/investigation",
        package="inspect-audit@git+https://example.org/ia@abc",
        models=["openai/openrouter/deepseek/flash"],
        task_args={"judge": {"grader": "openrouter/openai/gpt-6-luna"}, "logs": ["hawk:x"]},
    )
    for restricted in (False, True):
        config = EvalSetConfig.model_validate(hawk_config(job, restricted=restricted))
        assert [i.name for g in config.additional_models or [] for i in g.items] == [
            "openrouter/openai/gpt-6-luna"
        ]


def test_a_child_job_runs_as_this_runs_user_with_its_current_credentials(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """On a runner the refresh credential rotates when the access token refreshes.

    The child must get the rotated one, so the access token is fetched first; and
    it must be this run's own, so the child's calls bill to the same key. Uses
    Hawk 4's real hook; only the IdP exchange is faked.
    """
    import hawk.client
    from inspect_ai.hooks import _hooks

    received: dict[str, Any] = {}
    hook = _runner_hook(monkeypatch, tmp_path, lambda token: ("current-access", "rotated"))

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

    monkeypatch.setattr(_hooks, "get_all_hooks", lambda: [hook])
    monkeypatch.setattr(hawk.client, "HawkClient", Client)
    job = Job(label="child", task="benchmark/task", package="benchmark", models=["m/x"])
    assert asyncio.run(HawkRuns("https://hawk.example").submit(job)) == "child-job"
    assert received["token"] == "current-access"
    assert received["refresh_token"] == "rotated"
    # the child's own code may read Hawk; Hawk does not tell a runner its API
    assert received["config"].runner.environment == {"HAWK_API_URL": "https://hawk.example"}
    assert "secrets" not in received, "no provider key travels with a child job"
    # the mode is always stated: left out, Hawk falls back to a deployment flag (FRICTION 86)
    assert received["credential_mode"] == "user"


def test_every_submission_states_its_mode_and_matches_its_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import hawk.client

    from inspect_audit.dispatch.hawk import submit_eval_set

    received: dict[str, Any] = {}

    class Client:
        def __init__(self, **kwargs: Any) -> None:
            pass

        async def __aenter__(self) -> "Client":
            return self

        async def __aexit__(self, *args: Any) -> None:
            pass

        async def create_eval_set(self, config: Any, **kwargs: Any) -> dict[str, Any]:
            received.clear()
            received.update(kwargs, config=config)
            return {"eval_set_id": "job"}

    monkeypatch.setattr(hawk.client, "HawkClient", Client)
    config = {
        "tasks": [{"package": "p", "name": "p", "items": [{"name": "t"}]}],
        "runner": {"environment": {"KEEP": "1"}},
    }
    submit = lambda **kw: asyncio.run(  # noqa: E731
        submit_eval_set(config, api_url="https://hawk.example", token="t", **kw)
    )
    assert submit(credential_mode="restricted") == "job"
    assert received["credential_mode"] == "restricted" and "refresh_token" not in received
    assert received["config"].runner.environment == {
        "KEEP": "1",
        "HAWK_API_URL": "https://hawk.example",
    }
    assert config["runner"]["environment"] == {"KEEP": "1"}, "the caller's config is not changed"
    submit(credential_mode="user", refresh_token="r")
    assert received["credential_mode"] == "user" and received["refresh_token"] == "r"
    with pytest.raises(ValueError, match="refresh token"):
        submit(credential_mode="user")
    with pytest.raises(ValueError, match="refresh token"):
        submit(credential_mode="restricted", refresh_token="r")


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
    assert status == JobState.COMPLETE
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
        menu=Models(
            investigators=["openai/investigator"],
            subjects=["openai/subject"],
            judges=["openai/judge"],
        ),
        supplied=["hawk:supplied-logs"],
        source="benchmark.task",
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
        # what each run has used so far, as a backend reports it (token counts by model)
        self.used: dict[str, dict[str, Any]] = {}

    async def submit(self, job: Job) -> str:
        self.submitted.append(job)
        return f"run-{len(self.submitted)}"

    async def status(self, run_id: str) -> JobState:
        return JobState.RUNNING

    async def usage(self, run_id: str) -> tuple[dict[str, Any], int]:
        return self.used.get(run_id, {}), 0


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
    benchmark = _audit.run_eval(inv)
    out = asyncio.run(benchmark("smoke", ["1"], ["openai/subject"], "{}", 1, [], None, [], "why"))  # type: ignore[arg-type]
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
        asyncio.run(benchmark("other", None, ["openai/investigator"], "{}", 1, [], None, [], ""))  # type: ignore[arg-type]
    with pytest.raises(ToolError, match="exists"):
        asyncio.run(benchmark("smoke", None, ["openai/subject"], "{}", 1, [], None, [], ""))  # type: ignore[arg-type]


def test_an_investigation_reads_logs_by_job_label_or_supplied_source(
    tmp_path: Path, record: AuditRecord, monkeypatch: pytest.MonkeyPatch
) -> None:
    from inspect_ai.tool import ToolError

    runs = FakeRuns()
    inv = _make_audit(tmp_path, runs)
    inv.paper = "https://arxiv.org/abs/2601.00001"
    record.jobs.append({"label": "smoke", "kind": "benchmark", "run_id": "r", "address": "hawk:r"})
    investigation = _audit.send_investigators(inv)
    asyncio.run(
        investigation(  # type: ignore[arg-type]
            "a1",
            ["1"],
            ["gold-answer"],
            ["smoke", "hawk:supplied-logs"],
            "openai/investigator",
            '{"grader": "openai/judge"}',
            "Check every failed rubric item against the context.",
        )
    )
    [job] = runs.submitted
    # the auditor's note is the investigators' brief, not just a line in its record
    assert job.task_args["notes"] == "Check every failed rubric item against the context."
    # and the operator's paper goes with it (FRICTION 40)
    assert job.task_args["paper"] == "https://arxiv.org/abs/2601.00001"
    assert job.task == "inspect_audit/investigation" and job.extra_packages == ["benchmark"]
    assert job.task_args["logs"] == ["hawk:r", "hawk:supplied-logs"]
    assert job.task_args["judge"] == {"grader": "openai/judge"}
    assert job.task_args["source"] == "benchmark.task"
    # the benchmark goes into the investigators' own boxes as the job installs it (FRICTION 74)
    assert job.task_args["install"][0] == "benchmark"
    assert job.task_args["install"][-1].startswith("inspect-ai==")
    # uncapped, and no prices: the audit prices its jobs itself (FRICTION 100)
    assert "cost_limit" not in job.task_args and "prices" not in job.task_args
    with pytest.raises(ToolError, match="neither a job of yours nor a supplied source"):
        asyncio.run(
            investigation(
                "a2",
                ["1"],
                ["gold-answer"],
                ["/etc/passwd"],
                "openai/investigator",
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

    assert asyncio.run(run()) == JobState.COMPLETE
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
                },
            )
        authorizer = AuditAuthorizer(policy)
        if allowed:
            assert authorizer.authorise(job, kind, set()).task_args == job.task_args
        else:
            with pytest.raises(ToolError, match="task_args"):
                authorizer.authorise(job, kind, set())


def _runner_hook(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, exchange: Any) -> Any:
    """Hawk 4's RefreshTokenHook as a runner installs it; only the IdP call is faked.

    Its token cache goes under tmp_path, and `exchange(token)` stands in for the
    refresh-token grant, returning (access token, rotated refresh token or None).
    """
    import tempfile

    import pydantic
    from hawk.runner import human_token
    from hawk.runner import refresh_token as hawk_hook

    def fake(config: Any, token: pydantic.SecretStr) -> Any:
        access, rotated = exchange(token.get_secret_value())
        return human_token._Cache(  # pyright: ignore[reportPrivateUsage]
            version=1,
            identity=human_token.cache_identity(config),
            access_token=pydantic.SecretStr(access),
            expires_at=time.time() + 3600,
            refresh_token=pydantic.SecretStr(rotated) if rotated else token,
        )

    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    monkeypatch.setattr(human_token, "_exchange", fake)
    # what a runner's environment holds: its refresh settings, the gateway's
    # address and its own startup credential
    monkeypatch.setenv("HAWK_RUNNER_REFRESH_URL", "https://idp.example/token")
    monkeypatch.setenv("HAWK_RUNNER_REFRESH_CLIENT_ID", "client")
    monkeypatch.setenv("HAWK_RUNNER_REFRESH_TOKEN", "refresh-0")
    monkeypatch.setenv("AI_GATEWAY_BASE_URL", "https://middleman.example")
    monkeypatch.setenv("BASE_API_KEY", "startup-jwt")
    return hawk_hook.RefreshTokenHook()


def test_the_runner_credentials_come_from_hawks_real_hook(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Hawk's own RefreshTokenHook, as installed, not a copy of its behaviour.

    2026-10-20: a fake modelled on an older hook passed while hawk 3.7's hook declined
    the ask and the budget tool got no token on a runner. 2026-11-12: Hawk 4 moved the
    rotated refresh token from the hook into a file cache (FRICTION 70). Only the
    network refresh is replaced; the hook's own rules run as shipped.
    """
    from inspect_ai.hooks import _hooks

    refreshes: list[str] = []

    def exchange(token: str) -> tuple[str, str]:
        refreshes.append(token)
        return f"access-{len(refreshes)}", f"refresh-{len(refreshes)}"

    hook = _runner_hook(monkeypatch, tmp_path, exchange)
    monkeypatch.setattr(_hooks, "get_all_hooks", lambda: [hook])

    login = HawkLogin("https://hawk.example")
    assert asyncio.run(login.access_token()) == "access-1"
    # the child job gets the refresh token as rotated by that refresh
    assert asyncio.run(login.refresh_token()) == "refresh-1"
    assert refreshes == ["refresh-0"]


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
    from inspect_ai.hooks import _hooks

    from inspect_audit._log_sources import fetch_logs

    hook = _runner_hook(monkeypatch, tmp_path, lambda token: ("runner-access", None))
    monkeypatch.setenv("HAWK_API_URL", "https://hawk.example")
    monkeypatch.delenv("HAWK_ACCESS_TOKEN", raising=False)
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
    asyncio.run(
        _audit.send_investigators(inv)(  # type: ignore[arg-type]
            "a", ["1"], ["gold-answer"], [], None, "{}", ""
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
    assert asyncio.run(runs.status("child")) == JobState.COMPLETE


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


def test_budget_reports_what_the_audit_and_each_job_have_spent(
    tmp_path: Path, record: AuditRecord, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One owner (`Spend`): own calls plus every job's spend so far, running or not,
    priced from its token counts at the audit's prices (FRICTION 25, 99)."""
    from types import SimpleNamespace

    import inspect_ai.util
    from inspect_ai.model import ModelUsage

    from inspect_audit._spend import Spend

    _prices.register_price("openai/investigator", {"input": 1.0, "output": 2.0})
    runs = FakeRuns()
    runs.used = {
        # 1M in + 1M out at $1/$2 per M: $3, while still running
        "r1": {"openai/investigator": ModelUsage(input_tokens=1_000_000, output_tokens=1_000_000)},
        "r2": {"openai/unpriced": ModelUsage(input_tokens=5)},
    }
    record.jobs.append({"label": "screen", "kind": "investigation", "run_id": "r1", "address": "a"})
    record.jobs.append({"label": "odd", "kind": "benchmark", "run_id": "r2", "address": "b"})
    monkeypatch.setattr(
        inspect_ai.util,
        "sample_limits",
        lambda: SimpleNamespace(cost=SimpleNamespace(usage=5.82, limit=None)),
    )
    spend = Spend(runs, budget_usd=100.0)
    report = asyncio.run(_audit.budget(spend, None, openrouter=False)())  # type: ignore[call-arg]
    assert "Your own calls: $5.82" in report
    assert "screen (investigation, running): $3.00 so far" in report
    assert "odd (benchmark, running): $0.00 so far; no price for openai/unpriced" in report
    assert "Spent so far: $8.82" in report
    assert "$91.18 (91%) left" in report


def test_fetched_logs_land_in_the_same_folder_on_every_attempt(tmp_path: Path) -> None:
    """Their paths reach the investigation's plan and so its identity: a random folder
    made a restarted runner a different task that eval_set refused to resume (FRICTION 42)."""
    from inspect_audit._log_sources import stable_dir

    assert stable_dir("hawk", "set-a") == stable_dir("hawk", "set-a")
    assert stable_dir("hawk", "set-a") != stable_dir("hawk", "set-b")


def test_the_outcome_tells_the_operator_where_the_results_are() -> None:
    """v3's results sat on S3 with nothing pointing at them (FRICTION 27)."""
    from inspect_ai.scorer import Target
    from inspect_ai.solver import TaskState

    from inspect_audit._report import AuditState, audit_outcome

    state = TaskState(model="m", sample_id="audit", epoch=1, input="", messages=[])
    audit = state.store_as(AuditState)
    audit.outcome = "blocked"
    audit.outcome_reasons = ["publishing failed"]
    audit.saved = ["s3://bucket/evals/x/artifacts/u/findings"]
    score = asyncio.run(audit_outcome()(state, Target("")))
    assert score is not None and score.explanation
    assert "publishing failed" in score.explanation
    assert "s3://bucket/evals/x/artifacts/u/findings" in score.explanation
    assert "hawk download-artifacts" in score.explanation


def test_a_local_paper_is_read_as_a_file(tmp_path: Path) -> None:
    from inspect_audit._paper import paper_file

    (tmp_path / "cl-bench.pdf").write_bytes(b"%PDF-1.7")
    assert paper_file(str(tmp_path / "cl-bench.pdf")) == ("cl-bench.pdf", b"%PDF-1.7")
    with pytest.raises(OSError):
        paper_file("not a path or a url")


def test_every_argument_a_job_may_carry_is_one_its_task_accepts() -> None:
    """The launch contract and the task's signature drifted: `paper` was permitted and
    passed but the registered task did not take it, so every job would have failed."""
    import inspect as pyinspect

    from inspect_audit import _registry
    from inspect_audit.dispatch.policy import INVESTIGATION_ARGS

    accepted = set(pyinspect.signature(_registry.investigation).parameters)
    assert accepted >= INVESTIGATION_ARGS


def test_the_auditor_launches_a_regrade_as_its_judge(
    tmp_path: Path, record: AuditRecord, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Re-judging needs the judge's credentials, which only a job has (FRICTION 20)."""
    import inspect as pyinspect

    from inspect_ai.tool import ToolError

    from inspect_audit import _registry
    from inspect_audit.dispatch.policy import REGRADE_ARGS

    runs = FakeRuns()
    inv = _make_audit(tmp_path, runs)
    regrade = _audit.run_eval(inv)
    asyncio.run(
        regrade(  # type: ignore[arg-type]
            "rejudge",
            ["1"],
            [],
            '{"grader": "openai/judge"}',
            3,
            ["hawk:supplied-logs"],
            None,
            [],
            "noise",
        )
    )
    [job] = runs.submitted
    assert job.task == "inspect_audit/regrade" and job.models == ["openai/judge"]
    assert job.task_args["judge"] == {"grader": "openai/judge"} and job.task_args["epochs"] == 3
    assert job.task_args["logs"] == ["hawk:supplied-logs"]
    assert set(pyinspect.signature(_registry.regrade).parameters) >= REGRADE_ARGS
    # a grader that calls no model (SciCode's tests) is rescored too, under a mock model
    asyncio.run(
        regrade(  # type: ignore[arg-type]
            "rerun-tests", ["1"], [], "{}", 1, ["hawk:supplied-logs"], None, [], "tests"
        )
    )
    assert runs.submitted[-1].models == ["mockllm/model"]
    assert runs.submitted[-1].task_args["judge"] == {}
    # a variant's attempts are graded in its own run: rescoring them is refused at once
    record = _audit.store_as(AuditRecord)
    record.jobs = [
        *record.jobs,
        {"label": "v", "kind": "variant", "run_id": "x", "address": "hawk:x"},
    ]
    with pytest.raises(ToolError, match=r"not v \(variant\)"):
        asyncio.run(
            regrade(  # type: ignore[arg-type]
                "rv", ["1"], [], "{}", 1, ["v"], None, [], ""
            )
        )
    # no per-item cap and no prices: the audit prices its jobs itself (FRICTION 100)
    assert "cost_limit" not in runs.submitted[-1].task_args
    assert "prices" not in runs.submitted[-1].task_args
    # epochs are the auditor's call; only a non-positive count is refused
    with pytest.raises(ToolError, match="positive whole number"):
        asyncio.run(
            regrade(  # type: ignore[arg-type]
                "again",
                ["1"],
                [],
                '{"grader": "openai/judge"}',
                0,
                ["hawk:supplied-logs"],
                None,
                [],
                "",
            )
        )


def test_launches_stop_once_spend_so_far_reaches_the_budget(
    tmp_path: Path, record: AuditRecord, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No reservations: worst-case caps starved investigations and stopped the auditor
    with the budget unspent (FRICTION 25, 50, 52, 76). Measured spend alone decides."""
    from types import SimpleNamespace

    import inspect_ai.util
    from inspect_ai.tool import ToolError

    from inspect_audit.dispatch.policy import Limits

    runs = FakeRuns()
    inv = _make_audit(tmp_path, runs)
    policy = inv.authorizer.policy
    budgeted = policy.model_copy(update={"limits": Limits(budget_usd=10.0)})
    from inspect_ai.model import ModelUsage
    from inspect_ai.util import store_as

    from inspect_audit._spend import Spend
    from inspect_audit.dispatch.policy import AuditAuthorizer, PolicyStore, ensure_policy

    inv.authorizer = AuditAuthorizer(budgeted)
    inv.spend = Spend(runs, 10.0)
    saved = store_as(PolicyStore)
    saved.policy_json = saved.digest = None  # the fixture's policy, replaced by the budgeted one
    ensure_policy(budgeted, saved, require_existing=False)
    own = {"usage": 4.0}
    monkeypatch.setattr(
        inspect_ai.util,
        "sample_limits",
        lambda: SimpleNamespace(cost=SimpleNamespace(usage=own["usage"], limit=None)),
    )
    investigate = _audit.send_investigators(inv)

    def launch(label: str, samples: list[str]) -> str:
        return asyncio.run(
            investigate(  # type: ignore[arg-type]
                label,
                samples,
                ["gold-answer"],
                ["hawk:supplied-logs"],
                "openai/investigator",
                '{"grader": "openai/judge"}',
                "",
            )
        )

    # $4 spent; jobs launch, uncapped, nothing reserved
    launch("first", [str(i) for i in range(1, 21)])
    launch("second", ["21"])
    # a fresh rerun has no cap, and is no longer refused for it
    out = asyncio.run(
        _audit.run_eval(inv)("rerun", ["1"], ["openai/subject"], "{}", 1, [], None, [], "")  # type: ignore[arg-type]
    )
    assert "Launched" in out
    # once spend reaches the budget, launches stop: a running job counts what it has
    # spent so far, not only once collected (FRICTION 99)
    _prices.register_price("openai/investigator", {"input": 1.0, "output": 1.0})
    runs.used["run-1"] = {"openai/investigator": ModelUsage(input_tokens=6_500_000)}
    with pytest.raises(ToolError, match="budget is spent"):
        launch("third", ["22"])


def test_full_coverage_holds_submission_until_every_cell_has_a_take(
    tmp_path: Path, record: AuditRecord, monkeypatch: pytest.MonkeyPatch
) -> None:
    """v3 sampled 120 of 409 failures and stopped with $132 unspent (FRICTION 25)."""
    from inspect_ai.tool import ToolError

    from inspect_audit import _register
    from inspect_audit._findings import submit_findings
    from inspect_audit.dispatch.policy import AuditAuthorizer, Limits

    inv = _make_audit(tmp_path, FakeRuns())
    policy = inv.authorizer.policy
    inv.authorizer = AuditAuthorizer(policy.model_copy(update={"limits": Limits(coverage="full")}))
    gap = _register.Register(
        checks=["gold-answer"], samples={"1": {"gold-answer": []}, "2": {"gold-answer": []}}
    )

    async def loaded() -> _register.Register:
        return gap

    monkeypatch.setattr(_register, "load", loaded)
    with pytest.raises(ToolError, match="full coverage: 2 of 2"):
        asyncio.run(submit_findings(tmp_path, inv, "b/t")())  # type: ignore[call-arg]


class RefusingRuns(FakeRuns):
    """Hawk as v5 met it: the proxy lacks a model, so submissions and lookups fail."""

    async def submit(self, job: Job) -> str:
        raise RuntimeError("Middleman error: Models not found: ['openai/gpt-5.1']")

    async def status(self, run_id: str) -> str:
        raise ConnectionError("Hawk API unreachable")


def test_a_platform_refusal_reaches_the_auditor_not_the_eval(
    tmp_path: Path, record: AuditRecord
) -> None:
    # FRICTION 51: an exception here ended the audit with nothing saved
    from inspect_ai.tool import ToolError

    inv = _make_audit(tmp_path, RefusingRuns())
    benchmark = _audit.run_eval(inv)
    with pytest.raises(ToolError, match="Models not found"):
        asyncio.run(benchmark("smoke", ["1"], ["openai/subject"], "{}", 1, [], None, [], ""))  # type: ignore[arg-type]
    assert record.jobs == []

    record.jobs.append({"label": "old", "kind": "benchmark", "run_id": "r", "address": "hawk:r"})
    with pytest.raises(ToolError, match="unreachable"):
        asyncio.run(_audit.jobs(inv)("status", "old", None))  # type: ignore[arg-type]


def test_hawk_says_which_models_it_will_refuse(monkeypatch: pytest.MonkeyPatch) -> None:
    from inspect_audit.dispatch import hawk as hawk_backend
    from inspect_audit.dispatch.hawk import HawkRuns

    async def served(*args: object, **kwargs: object) -> list[dict[str, object]]:
        # public names as our proxy listed them on 2026-11-12 (FRICTION 71)
        return [
            {"name": "gpt-6-luna"},
            {"name": "gpt-5.1"},
            {"name": "anthropic/claude-opus-5.5"},
            {"name": "openrouter/anthropic/claude-opus-5.5"},
            {"name": "gpt-4", "dead": True},
        ]

    runs = HawkRuns("https://api.example")

    async def token() -> str:
        return "t"

    monkeypatch.setattr(runs.login, "access_token", token)
    monkeypatch.setattr(hawk_backend, "middleman_request", served)
    asked = [
        "openai/gpt-6-luna",  # served as gpt-6-luna
        "openai/gpt-5.1",  # served as gpt-5.1: Hawk 4 accepts it
        "anthropic/claude-opus-5.5",  # Hawk 4 looks up claude-opus-5.5: not served
        "openai-api/openrouter/openrouter/anthropic/claude-opus-5.5",
        "openai/gpt-4",  # dead
    ]
    missing = asyncio.run(runs.unservable(asked))
    assert missing == ["anthropic/claude-opus-5.5", "openai/gpt-4"]


def test_an_audit_naming_unservable_models_stops_before_it_spends() -> None:
    # FRICTION 53: v5 learned this at its first regrade, 40 minutes in

    class Proxy:
        def __init__(self, missing: list[str]) -> None:
            self.missing, self.asked = missing, []

        async def unservable(self, models: list[str]) -> list[str]:
            self.asked.append(models)
            return self.missing

    named = ["openai/gpt-6-luna", "openai/gpt-5.1"]
    blocked = Proxy(["openai/gpt-5.1"])
    with pytest.raises(ValueError, match=r"openai/gpt-5\.1"):
        _audit.require_models(named, blocked)  # type: ignore[arg-type]
    assert blocked.asked == [named]
    _audit.require_models(named, Proxy([]))  # type: ignore[arg-type]
    _audit.require_models(named, None)


def test_middleman_calls_send_a_supplied_token_and_refuse_redirects(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Hawk 4's own middleman helper refuses tokens not read from a saved login in this
    process (FRICTION 70); ours sends the given one, and never across a redirect."""
    import hawk.cli.config
    from aiohttp import web
    from aiohttp.test_utils import TestServer

    from inspect_audit.dispatch.hawk import middleman_request

    seen: list[str] = []

    async def models(request: web.Request) -> web.Response:
        seen.append(request.headers["Authorization"])
        return web.json_response([{"name": "openai/gpt-6-luna"}])

    async def moved(request: web.Request) -> web.Response:
        raise web.HTTPFound("/elsewhere")

    async def run() -> None:
        app = web.Application()
        app.router.add_post("/permitted_models_info", models)
        app.router.add_put("/moved", moved)
        async with TestServer(app) as server:
            base = str(server.make_url("")).rstrip("/")
            config = hawk.cli.config.CliConfig.model_construct(middleman_url=base)
            monkeypatch.setattr(hawk.cli.config, "get_config", lambda: config)
            got = await middleman_request(
                "https://api.example", "post", "/permitted_models_info", "supplied", {}
            )
            assert got == [{"name": "openai/gpt-6-luna"}]
            with pytest.raises(RuntimeError, match="302"):
                await middleman_request("https://api.example", "put", "/moved", "supplied", {})

    asyncio.run(run())
    assert seen == ["Bearer supplied"]


def test_hawk_status_words_are_read_in_one_place() -> None:
    from inspect_audit.dispatch import FINISHED, JobKind, JobState
    from inspect_audit.dispatch.hawk import hawk_state

    assert hawk_state("complete") is JobState.COMPLETE
    assert hawk_state("failed") is hawk_state("deleted") is hawk_state(None) is JobState.FAILED
    assert hawk_state("stuck") is JobState.STUCK and JobState.STUCK not in FINISHED
    assert hawk_state("pending") is JobState.RUNNING
    assert JobKind.of("inspect_audit/regrade") is JobKind.REGRADE
    assert JobKind.of("inspect_evals/anything") is None and JobKind.BENCHMARK.task is None
