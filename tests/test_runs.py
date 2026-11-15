"""Child jobs: one description, run on Hawk or locally, recorded in the sample store."""

import asyncio
import time
from pathlib import Path
from typing import Any

import pytest
from test_helpers.audit import make_audit

from inspect_audit import _audit, _prices, _spend
from inspect_audit._report import AuditRecord
from inspect_audit.dispatch import (
    FINISHED,
    HawkLogin,
    HawkRuns,
    Job,
    JobKind,
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


def _submitted(hawk: Any, run_id: str | None = None) -> tuple[str, dict[str, Any], list[str]]:
    """What Hawk received for a job: its task's name, its arguments, its models."""
    config = hawk.jobs[run_id or list(hawk.jobs)[-1]].config
    [item] = config["tasks"][0]["items"]
    models = [m["name"] for g in config["models"] for m in g["items"]]
    return item["name"], item.get("args") or {}, models


@pytest.fixture
def record() -> AuditRecord:
    from inspect_ai.util import Store, store_as
    from inspect_ai.util._store import init_subtask_store

    init_subtask_store(Store())
    return store_as(AuditRecord)


def test_launched_jobs_are_recorded_and_only_allowed_models_run(
    tmp_path: Path, record: AuditRecord, hawk: Any
) -> None:
    inv = _make_audit(tmp_path, HawkRuns(hawk.url, credential_mode="restricted"))
    benchmark = _audit.run_eval(inv)
    out = asyncio.run(benchmark("smoke", ["1"], ["openai/subject"], "{}", 1, [], None, [], "why"))  # type: ignore[arg-type]
    [run_id] = hawk.jobs
    assert f"hawk:{run_id}" in out
    assert record.jobs == [
        {
            "label": "smoke",
            "kind": "benchmark",
            "run_id": run_id,
            "address": f"hawk:{run_id}",
            "note": "why",
            "policy_sha256": inv.authorizer.policy.digest,
        }
    ]
    # what Hawk received: the benchmark's task on the one sample, with the subject model
    config = hawk.jobs[run_id].config
    [item] = config["tasks"][0]["items"]
    assert item["sample_ids"] == ["1"]
    assert [m["name"] for g in config["models"] for m in g["items"]] == ["openai/subject"]
    from inspect_ai.tool import ToolError

    with pytest.raises(ToolError, match="may not be evaluated"):
        asyncio.run(benchmark("other", None, ["openai/investigator"], "{}", 1, [], None, [], ""))  # type: ignore[arg-type]
    with pytest.raises(ToolError, match="exists"):
        asyncio.run(benchmark("smoke", None, ["openai/subject"], "{}", 1, [], None, [], ""))  # type: ignore[arg-type]
    assert len(hawk.jobs) == 1, "a refused launch reaches Hawk with nothing"


def test_the_audits_end_stops_its_running_jobs(
    tmp_path: Path, record: AuditRecord, hawk: Any
) -> None:
    from test_helpers.fake_hawk import FakeJob

    inv = _make_audit(tmp_path, HawkRuns(hawk.url, credential_mode="restricted"))
    for label, status in [
        ("running", "running"),
        ("done", "complete"),
        ("stuck", "stuck"),
        ("asked", "running"),
    ]:
        hawk.jobs[label] = FakeJob(config={}, body={}, status=status)
    record.jobs = [
        {"label": label, "run_id": label, **extra}
        for label, extra in [
            ("running", {}),
            ("done", {}),
            ("stuck", {}),
            ("asked", {"stop_requested": True}),
            ("lost", {}),  # Hawk no longer knows it: it cannot be stopped
        ]
    ]
    assert asyncio.run(inv.stop_unsettled(record)) == ["running"]
    assert [label for label, job in hawk.jobs.items() if job.stopped] == ["running"]
    by_label = {j["label"]: j for j in record.jobs}
    assert by_label["running"]["stopped_by"] == "audit end"
    # a job that could not be stopped is left unmarked, so a resumed audit still sees it
    assert "stop_requested" not in by_label["lost"]
    assert "stop_requested" not in by_label["done"]


def test_an_investigation_reads_logs_by_job_label_or_supplied_source(
    tmp_path: Path, record: AuditRecord, hawk: Any
) -> None:
    from inspect_ai.tool import ToolError

    inv = _make_audit(tmp_path, HawkRuns(hawk.url, credential_mode="restricted"))
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
    name, args, models = _submitted(hawk)
    assert name == "investigation" and models == ["openai/investigator"]
    assert "benchmark" in hawk.jobs[list(hawk.jobs)[-1]].config["packages"]
    # the auditor's note is the investigators' brief, not just a line in its record
    assert args["notes"] == "Check every failed rubric item against the context."
    # and the operator's paper goes with it (FRICTION 40)
    assert args["paper"] == "https://arxiv.org/abs/2601.00001"
    assert args["logs"] == ["hawk:r", "hawk:supplied-logs"]
    assert args["judge"] == {"grader": "openai/judge"}
    assert args["source"] == "benchmark.task"
    # the benchmark goes into the investigators' own boxes as the job installs it (FRICTION 74)
    assert args["install"][0] == "benchmark" and args["install"][-1].startswith("inspect-ai==")
    # uncapped, and no prices: the audit prices its jobs itself (FRICTION 100)
    assert "cost_limit" not in args and "prices" not in args
    with pytest.raises(ToolError, match="neither a job of yours nor a supplied source"):
        asyncio.run(
            investigation(
                "a2", ["1"], ["gold-answer"], ["/etc/passwd"], "openai/investigator", "{}", ""
            )  # type: ignore[arg-type]
        )
    assert len(hawk.jobs) == 1


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
    [line] = asyncio.run(_spend.key_balance(None, openrouter=True))
    assert "unknown" in line and "$0.00" not in line


def test_an_empty_judge_reaches_the_investigation_as_empty(
    tmp_path: Path, record: AuditRecord, hawk: Any
) -> None:
    """{} means "their grader calls no model"; None means "infer it from the logs"."""
    inv = _make_audit(tmp_path, HawkRuns(hawk.url, credential_mode="restricted"))
    asyncio.run(
        _audit.send_investigators(inv)("a", ["1"], ["gold-answer"], [], None, "{}", "")  # type: ignore[arg-type]
    )
    assert _submitted(hawk)[1]["judge"] == {}


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
    tmp_path: Path, record: AuditRecord, hawk: Any
) -> None:
    from inspect_ai.util import Store
    from inspect_ai.util._store import init_subtask_store

    audit = _make_audit(tmp_path, HawkRuns(hawk.url, credential_mode="restricted"))
    init_subtask_store(Store())
    job = Job(label="child", task="benchmark/task", package="benchmark", models=["openai/subject"])
    with pytest.raises(RuntimeError, match="differs from its saved launch policy"):
        asyncio.run(audit.launch(job, JobKind.BENCHMARK, "unbound"))
    assert not hawk.jobs


def test_budget_reports_what_the_audit_and_each_job_have_spent(
    tmp_path: Path, record: AuditRecord, monkeypatch: pytest.MonkeyPatch, hawk: Any
) -> None:
    """One owner (`Spend`): own calls plus every job's spend so far, running or not,
    priced from its token counts at the audit's prices (FRICTION 25, 99)."""
    from types import SimpleNamespace

    import inspect_ai.util
    from test_helpers.fake_hawk import FakeJob

    from inspect_audit._spend import Spend

    _prices.register_price("openai/investigator", {"input": 1.0, "output": 2.0})
    # 1M in + 1M out at $1/$2 per M: $3, while still running
    big = {
        "openai/investigator": {
            "input_tokens": 1_000_000,
            "output_tokens": 1_000_000,
            "total_tokens": 2_000_000,
        }
    }
    hawk.jobs["r1"] = FakeJob(config={}, body={}, samples=[(False, big)])
    hawk.jobs["r2"] = FakeJob(
        config={},
        body={},
        samples=[(False, {"openai/unpriced": {"input_tokens": 5, "total_tokens": 5}})],
    )
    record.jobs.append({"label": "screen", "kind": "investigation", "run_id": "r1", "address": "a"})
    record.jobs.append({"label": "odd", "kind": "benchmark", "run_id": "r2", "address": "b"})
    # the auditor's own calls, as Inspect accounts them for the running sample
    monkeypatch.setattr(
        inspect_ai.util,
        "sample_limits",
        lambda: SimpleNamespace(cost=SimpleNamespace(usage=5.82, limit=None)),
    )
    spend = Spend(HawkRuns(hawk.url, credential_mode="restricted"), budget_usd=100.0)
    report = asyncio.run(_spend.budget(spend)())  # type: ignore[call-arg]
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
    tmp_path: Path, record: AuditRecord, hawk: Any
) -> None:
    """Re-judging needs the judge's credentials, which only a job has (FRICTION 20)."""
    import inspect as pyinspect

    from inspect_ai.tool import ToolError

    from inspect_audit import _registry
    from inspect_audit.dispatch.policy import REGRADE_ARGS

    inv = _make_audit(tmp_path, HawkRuns(hawk.url, credential_mode="restricted"))
    regrade = _audit.run_eval(inv)
    asyncio.run(
        regrade(
            "rejudge",
            ["1"],
            [],
            '{"grader": "openai/judge"}',
            3,
            ["hawk:supplied-logs"],
            None,
            [],
            "noise",
        )  # type: ignore[arg-type]
    )
    name, args, models = _submitted(hawk)
    assert name == "regrade" and models == ["openai/judge"]
    assert args["judge"] == {"grader": "openai/judge"} and args["epochs"] == 3
    assert args["logs"] == ["hawk:supplied-logs"]
    assert set(pyinspect.signature(_registry.regrade).parameters) >= REGRADE_ARGS
    # no per-item cap and no prices: the audit prices its jobs itself (FRICTION 100)
    assert "cost_limit" not in args and "prices" not in args
    # a grader that calls no model (SciCode's tests) is rescored too, under a mock model
    asyncio.run(
        regrade("rerun-tests", ["1"], [], "{}", 1, ["hawk:supplied-logs"], None, [], "tests")  # type: ignore[arg-type]
    )
    _, args, models = _submitted(hawk)
    assert models == ["mockllm/model"] and args["judge"] == {}
    # a variant's attempts are graded in its own run: rescoring them is refused at once
    record.jobs = [
        *record.jobs,
        {"label": "v", "kind": "variant", "run_id": "x", "address": "hawk:x"},
        {"label": "r", "kind": "regrade", "run_id": "y", "address": "hawk:y"},
    ]
    before = len(hawk.jobs)
    with pytest.raises(ToolError, match="v is a variant job"):
        asyncio.run(regrade("rv", ["1"], [], "{}", 1, ["v"], None, [], ""))  # type: ignore[arg-type]
    # nor can an investigation read a regrade's logs (breadth round, mathvista)
    with pytest.raises(ToolError, match="r is a regrade job"):
        asyncio.run(
            _audit.send_investigators(inv)("i", ["1"], ["gold-answer"], ["r"], None, "{}", "")  # type: ignore[arg-type]
        )
    assert len(hawk.jobs) == before, "refused before any launch"
    # epochs are the auditor's call; only a non-positive count is refused
    with pytest.raises(ToolError, match="positive whole number"):
        asyncio.run(
            regrade(
                "again",
                ["1"],
                [],
                '{"grader": "openai/judge"}',
                0,
                ["hawk:supplied-logs"],
                None,
                [],
                "",
            )  # type: ignore[arg-type]
        )
    assert len(hawk.jobs) == 2


def test_launches_stop_once_spend_so_far_reaches_the_budget(
    tmp_path: Path, record: AuditRecord, monkeypatch: pytest.MonkeyPatch, hawk: Any
) -> None:
    """No reservations: worst-case caps starved investigations and stopped the auditor
    with the budget unspent (FRICTION 25, 50, 52, 76). Measured spend alone decides."""
    from types import SimpleNamespace

    import inspect_ai.util
    from inspect_ai.tool import ToolError
    from inspect_ai.util import store_as

    from inspect_audit._spend import Spend
    from inspect_audit.dispatch.policy import AuditAuthorizer, Limits, PolicyStore, ensure_policy

    runs = HawkRuns(hawk.url, credential_mode="restricted")
    inv = _make_audit(tmp_path, runs)
    budgeted = inv.authorizer.policy.model_copy(update={"limits": Limits(budget_usd=10.0)})
    inv.authorizer = AuditAuthorizer(budgeted)
    inv.spend = Spend(inv.runs, 10.0)
    saved = store_as(PolicyStore)
    saved.policy_json = saved.digest = None  # the fixture's policy, replaced by the budgeted one
    ensure_policy(budgeted, saved, require_existing=False)
    # the auditor's own calls so far, as Inspect accounts them
    monkeypatch.setattr(
        inspect_ai.util,
        "sample_limits",
        lambda: SimpleNamespace(cost=SimpleNamespace(usage=4.0, limit=None)),
    )
    investigate = _audit.send_investigators(inv)

    def launch(label: str, samples: list[str]) -> str:
        return asyncio.run(
            investigate(
                label,
                samples,
                ["gold-answer"],
                ["hawk:supplied-logs"],
                "openai/investigator",
                '{"grader": "openai/judge"}',
                "",
            )  # type: ignore[arg-type]
        )

    # $4 spent; jobs launch, uncapped, nothing reserved
    launch("first", [str(i) for i in range(1, 21)])
    launch("second", ["21"])
    out = asyncio.run(
        _audit.run_eval(inv)("rerun", ["1"], ["openai/subject"], "{}", 1, [], None, [], "")  # type: ignore[arg-type]
    )
    assert "Launched" in out
    # once spend reaches the budget, launches stop: a running job counts what it has
    # spent so far, as Hawk reports it, not only once collected (FRICTION 99)
    _prices.register_price("openai/investigator", {"input": 1.0, "output": 1.0})
    first = record.jobs[0]["run_id"]
    hawk.progress(
        first,
        [(False, {"openai/investigator": {"input_tokens": 6_500_000, "total_tokens": 6_500_000}})],
    )
    with pytest.raises(ToolError, match="budget is spent"):
        launch("third", ["22"])
    assert len(hawk.jobs) == 3


def test_full_coverage_holds_submission_until_every_cell_has_a_take(
    tmp_path: Path, record: AuditRecord, hawk: Any
) -> None:
    """v3 sampled 120 of 409 failures and stopped with $132 unspent (FRICTION 25)."""
    from inspect_ai.tool import ToolError

    from inspect_audit import _register
    from inspect_audit._findings import submit_findings
    from inspect_audit.dispatch.policy import AuditAuthorizer, Limits

    inv = _make_audit(tmp_path, HawkRuns(hawk.url, credential_mode="restricted"))
    policy = inv.authorizer.policy
    inv.authorizer = AuditAuthorizer(policy.model_copy(update={"limits": Limits(coverage="full")}))
    # the population, with no verdict collected for either item
    _register.save(
        _register.Register(
            checks=["gold-answer"], samples={"1": {"gold-answer": []}, "2": {"gold-answer": []}}
        )
    )
    with pytest.raises(ToolError, match="full coverage: 2 of 2"):
        asyncio.run(submit_findings(tmp_path, inv, "b/t")())  # type: ignore[call-arg]


def test_a_platform_refusal_reaches_the_auditor_not_the_eval(
    tmp_path: Path, record: AuditRecord, hawk: Any
) -> None:
    # FRICTION 51: an exception here ended the audit with nothing saved
    from inspect_ai.tool import ToolError

    inv = _make_audit(tmp_path, HawkRuns(hawk.url, credential_mode="restricted"))
    benchmark = _audit.run_eval(inv)
    # Hawk as v5 met it: its proxy lacked a model, so it refused the submission
    hawk.refuse = "Middleman error: Models not found: ['openai/gpt-5.1']"
    with pytest.raises(ToolError, match="Models not found"):
        asyncio.run(benchmark("smoke", ["1"], ["openai/subject"], "{}", 1, [], None, [], ""))  # type: ignore[arg-type]
    assert record.jobs == []

    hawk.refuse, hawk.down = None, True
    record.jobs.append({"label": "old", "kind": "benchmark", "run_id": "r", "address": "hawk:r"})
    with pytest.raises(ToolError, match=r"503|unreachable"):
        asyncio.run(_audit.jobs(inv)("status", "old", None))  # type: ignore[arg-type]


def test_hawk_says_which_models_it_will_refuse(hawk: Any) -> None:
    # public names as our proxy listed them on 2026-11-12 (FRICTION 71)
    hawk.served = [
        "gpt-6-luna",
        "gpt-5.1",
        "anthropic/claude-opus-5.5",
        "openrouter/anthropic/claude-opus-5.5",
        {"name": "gpt-4", "dead": True},
    ]
    asked = [
        "openai/gpt-6-luna",  # served as gpt-6-luna
        "openai/gpt-5.1",  # served as gpt-5.1: Hawk 4 accepts it
        "anthropic/claude-opus-5.5",  # Hawk 4 looks up claude-opus-5.5: not served
        "openai-api/openrouter/openrouter/anthropic/claude-opus-5.5",
        "openai/gpt-4",  # dead
    ]
    missing = asyncio.run(HawkRuns(hawk.url, credential_mode="restricted").unservable(asked))
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


def test_hawk_status_words_are_read_in_one_place() -> None:
    from inspect_audit.dispatch import FINISHED, JobKind, JobState
    from inspect_audit.dispatch.hawk import hawk_state

    assert hawk_state("complete") is JobState.COMPLETE
    assert hawk_state("failed") is hawk_state("deleted") is JobState.FAILED
    # a 404 is unknown, not failed: a job Hawk briefly loses is not done (FRICTION 116)
    assert hawk_state(None) is JobState.UNKNOWN and JobState.UNKNOWN not in FINISHED
    assert hawk_state("stuck") is JobState.STUCK and JobState.STUCK not in FINISHED
    assert hawk_state("pending") is JobState.RUNNING
    assert JobKind.of("inspect_audit/regrade") is JobKind.REGRADE
    assert JobKind.of("inspect_evals/anything") is None and JobKind.BENCHMARK.task is None


def test_spend_that_cannot_be_read_stops_launches(
    tmp_path: Path, record: AuditRecord, monkeypatch: pytest.MonkeyPatch, hawk: Any
) -> None:
    """A failed lookup counted as $0: a review launched a job at $9 of $10 with Hawk
    unreachable. Unknown spend is unknown, and the budget cannot be checked (FRICTION 114)."""
    from types import SimpleNamespace

    import inspect_ai.util
    from inspect_ai.tool import ToolError
    from test_helpers.fake_hawk import FakeJob

    from inspect_audit._spend import Spend

    monkeypatch.setattr(
        inspect_ai.util,
        "sample_limits",
        lambda: SimpleNamespace(cost=SimpleNamespace(usage=9.0, limit=None)),
    )
    hawk.jobs["r1"] = FakeJob(config={}, body={})
    record.jobs.append({"label": "running", "kind": "benchmark", "run_id": "r1", "address": "a"})
    hawk.down = True
    spend = Spend(HawkRuns(hawk.url, credential_mode="restricted"), 10.0)
    with pytest.raises(ToolError, match="cannot read what running spent"):
        asyncio.run(spend.refuse_if_spent())
    assert "running (benchmark, unknown): spend unknown" in "\n".join(asyncio.run(spend.report()))


def test_a_flaky_backend_reaches_the_auditor_wherever_it_is_called(
    tmp_path: Path, record: AuditRecord, hawk: Any
) -> None:
    """A status check outside the old per-tool wrapper (the concurrency cap) let a
    ConnectionError end the sample (FRICTION 115); every backend call is wrapped now."""
    from inspect_ai.tool import ToolError
    from inspect_ai.util import store_as
    from test_helpers.fake_hawk import FakeJob

    from inspect_audit.dispatch.policy import AuditAuthorizer, Limits, PolicyStore, ensure_policy

    inv = _make_audit(tmp_path, HawkRuns(hawk.url, credential_mode="restricted"))
    capped = inv.authorizer.policy.model_copy(update={"limits": Limits(max_concurrent_jobs=1)})
    inv.authorizer = AuditAuthorizer(capped)
    saved = store_as(PolicyStore)
    saved.policy_json = saved.digest = None
    ensure_policy(capped, saved, require_existing=False)
    hawk.jobs["r"] = FakeJob(config={}, body={})
    record.jobs.append({"label": "old", "kind": "benchmark", "run_id": "r", "address": "hawk:r"})
    hawk.down = True
    with pytest.raises(ToolError, match=r"status\(r\) failed"):
        asyncio.run(
            _audit.run_eval(inv)("new", ["1"], ["openai/subject"], "{}", 1, [], None, [], "")  # type: ignore[arg-type]
        )
    assert list(hawk.jobs) == ["r"], "nothing launched past an unreadable concurrency check"


def test_a_job_is_settled_only_when_it_can_produce_nothing_more() -> None:
    """Submission waits for settled jobs: finished, stuck (Hawk says it makes no progress),
    or stopped by the auditor and gone from Hawk. A 404 alone is not settled, and a stuck
    job no longer blocks submission for ever (FRICTION 116)."""
    from inspect_audit.dispatch.jobs import settled

    assert all(settled(s, False) for s in (JobState.COMPLETE, JobState.FAILED, JobState.STOPPED))
    assert settled(JobState.STUCK, False)
    assert not settled(JobState.UNKNOWN, False) and settled(JobState.UNKNOWN, True)
    assert not settled(JobState.RUNNING, True)


def test_a_selection_is_never_empty_and_positions_start_at_1(record: AuditRecord) -> None:
    from inspect_ai.tool import ToolError

    from inspect_audit._register import Register, save

    # an empty filter would reach Hawk as no filter: every item
    with pytest.raises(ToolError, match="empty"):
        _audit._selection([])
    save(Register(subject={"ids": "positional"}, checks=["c"], samples={"1": {}, "2": {}}))
    with pytest.raises(ToolError, match="empty"):
        _audit._selection([])
    with pytest.raises(ToolError, match="from 1"):
        _audit._selection(["0", "1"])
    assert _audit._selection(["2", "1"]) == (None, (1, 2))
