"""`HawkRuns` against a Hawk API on localhost, through Hawk's own client.

Nothing of ours is patched: the real submission, status, usage, log download and stop
paths make real HTTP requests, and the fake answers with Hawk's own response models.
"""

import asyncio
from pathlib import Path

import pytest
from inspect_ai import Task, eval
from inspect_ai.dataset import Sample
from test_helpers.fake_hawk import FakeHawk, runner_hook

from inspect_audit.dispatch import HawkLogin, HawkRuns, Job, JobState
from inspect_audit.dispatch.jobs import settled


@pytest.fixture(autouse=True)
def token(monkeypatch: pytest.MonkeyPatch) -> None:
    # the operator's token as a runner or CI gets it: never this machine's saved login
    monkeypatch.setenv("HAWK_ACCESS_TOKEN", "test-token")
    # this machine's Hawk settings name the real deployment's proxy: nothing here may reach it
    monkeypatch.setenv("HAWK_MIDDLEMAN_URL", "http://127.0.0.1:9/not-the-real-proxy")


def _log(directory: Path) -> Path:
    [log] = eval(
        Task(dataset=[Sample(input="hi", target="hi", id="1")]),
        model="mockllm/model",
        log_dir=str(directory),
        display="none",
    )
    return Path(log.location)


JOB = Job(label="smoke", task="pkg/task", package="pkg", models=["mockllm/model"])


def test_a_job_runs_reports_usage_finishes_and_hands_back_its_logs(tmp_path: Path) -> None:
    log = _log(tmp_path / "made")

    async def run() -> None:
        async with FakeHawk() as hawk:
            runs = HawkRuns(hawk.url, credential_mode="restricted")
            run_id = await runs.submit(JOB)
            submitted = hawk.jobs[run_id].body
            assert submitted["credential_mode"] == "restricted"
            assert "refresh_token" not in submitted
            # the runner is told where Hawk is; Hawk itself does not say
            config = hawk.jobs[run_id].config
            assert config["runner"]["environment"]["HAWK_API_URL"] == hawk.url

            assert await runs.status(run_id) is JobState.RUNNING
            used = {"openai/x": {"input_tokens": 100, "output_tokens": 20, "total_tokens": 120}}
            # Hawk's live status, sample by sample: finished, still running, not started
            hawk.progress(run_id, [(True, used), (False, used), (False, None)])
            usage, done = await runs.usage(run_id)
            assert done == 1
            assert (usage["openai/x"].input_tokens, usage["openai/x"].output_tokens) == (200, 40)

            hawk.finish(run_id, logs=[log])
            assert await runs.status(run_id) is JobState.COMPLETE
            files = await runs.logs(run_id, tmp_path / "collected")
            assert [f.name for f in files] == [log.name]
            assert files[0].read_bytes() == log.read_bytes()
            assert all(t == "Bearer test-token" for t in hawk.tokens_seen)

    asyncio.run(run())


def test_hawk_losing_a_job_is_unknown_and_settles_only_once_stopped(tmp_path: Path) -> None:
    """A 404 is not a failure (FRICTION 116): the job may still be spending."""

    async def run() -> None:
        async with FakeHawk() as hawk:
            runs = HawkRuns(hawk.url, credential_mode="restricted")
            run_id = await runs.submit(JOB)
            hawk.forget(run_id)
            state = await runs.status(run_id)
            assert state is JobState.UNKNOWN
            assert not settled(state, stop_requested=False)
            assert settled(state, stop_requested=True)

    asyncio.run(run())


def test_a_stuck_job_settles_and_a_stopped_one_finishes(tmp_path: Path) -> None:
    async def run() -> None:
        async with FakeHawk() as hawk:
            runs = HawkRuns(hawk.url, credential_mode="restricted")
            stuck, stopped = await runs.submit(JOB), await runs.submit(JOB)
            hawk.stuck(stuck)
            assert settled(await runs.status(stuck), stop_requested=False)
            await runs.stop(stopped)
            assert hawk.jobs[stopped].stopped
            assert await runs.status(stopped) is JobState.FAILED  # stopped before any log
            assert await runs.logs(stopped, tmp_path / "none") == []

    asyncio.run(run())


def test_models_the_proxy_does_not_serve_are_named_before_any_spend() -> None:
    async def run() -> None:
        async with FakeHawk(served=["gpt-6-luna"]) as hawk:
            runs = HawkRuns(hawk.url, credential_mode="restricted")
            missing = await runs.unservable(["openai/gpt-6-luna", "openai/not-served"])
            assert missing == ["openai/not-served"]

    asyncio.run(run())


def test_proxy_calls_use_the_deployments_proxy_the_supplied_token_and_no_redirects() -> None:
    """Hawk 4's own helper refuses tokens it did not read from a saved login (FRICTION
    70); ours sends the one it is given, to the proxy the deployment names, not one a
    local HAWK_MIDDLEMAN_URL names (FRICTION 129), and never follows a redirect with it."""
    from inspect_audit.dispatch.hawk import middleman_request

    async def run() -> None:
        async with FakeHawk(served=["gpt-6-luna"]) as hawk:
            got = await middleman_request(
                hawk.url, "post", "/permitted_models_info", "supplied", {"api_key": "supplied"}
            )
            assert got == [{"name": "gpt-6-luna"}]
            assert hawk.tokens_seen == ["Bearer supplied"]
            with pytest.raises(RuntimeError, match="302"):
                await middleman_request(hawk.url, "put", "/moved", "supplied", {})

    asyncio.run(run())


def test_on_a_runner_credentials_come_from_hawks_own_hook(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Hawk's RefreshTokenHook as a runner installs it, refreshing over HTTP.

    2026-10-20: a fake modelled on an older hook passed while hawk 3.7's hook declined
    the ask. 2026-11-12: Hawk 4 moved the rotated refresh token into a file cache
    (FRICTION 70). Here nothing of Hawk's is replaced, only its identity provider.
    """

    async def run() -> None:
        async with FakeHawk() as hawk:
            runner_hook(monkeypatch, hawk, tmp_path)
            login = HawkLogin(hawk.url)
            assert await login.access_token() == "access-1"
            # a child gets the refresh token as rotated by that refresh, not the startup one
            assert await login.refresh_token() == "refresh-1"
            assert hawk.grants == ["refresh-0"]

    asyncio.run(run())


def test_a_child_job_runs_as_this_runs_user_with_its_current_credentials(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The access token first (refreshing may rotate the refresh token), then the
    rotated refresh token to the child, so it renews as this run's user."""

    async def run() -> None:
        async with FakeHawk() as hawk:
            runner_hook(monkeypatch, hawk, tmp_path)
            run_id = await HawkRuns(hawk.url).submit(JOB)
            body = hawk.jobs[run_id].body
            assert hawk.tokens_seen[-1] == "Bearer access-1"
            assert body["refresh_token"] == "refresh-1"
            # the mode is always stated: left out, Hawk uses a deployment flag (FRICTION 86)
            assert body["credential_mode"] == "user"
            assert body["secrets"] == {}, "no provider key travels with a child job"

    asyncio.run(run())


def test_every_submission_states_its_mode_and_matches_its_credentials() -> None:
    from inspect_audit.dispatch.hawk import submit_eval_set

    config = {
        "tasks": [{"package": "p", "name": "p", "items": [{"name": "t"}]}],
        "runner": {"environment": {"KEEP": "1"}},
    }

    async def run() -> None:
        async with FakeHawk() as hawk:

            async def submit(**kw: object) -> dict[str, object]:
                run_id = await submit_eval_set(config, api_url=hawk.url, token="t", **kw)  # type: ignore[arg-type]
                return hawk.jobs[run_id].body

            body = await submit(credential_mode="restricted")
            assert body["credential_mode"] == "restricted" and "refresh_token" not in body
            assert body["eval_set_config"]["runner"]["environment"] == {
                "KEEP": "1",
                "HAWK_API_URL": hawk.url,
            }
            assert config["runner"]["environment"] == {"KEEP": "1"}, (
                "the caller's config is unchanged"
            )
            body = await submit(credential_mode="user", refresh_token="r")
            assert body["credential_mode"] == "user" and body["refresh_token"] == "r"
            with pytest.raises(ValueError, match="refresh token"):
                await submit(credential_mode="user")
            with pytest.raises(ValueError, match="refresh token"):
                await submit(credential_mode="restricted", refresh_token="r")

    asyncio.run(run())


def test_hawk_logs_are_fetched_with_the_runners_own_credentials(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A child investigation fetching `hawk:` logs on a runner uses Hawk's real hook."""
    from inspect_audit._log_sources import fetch_logs

    log = _log(tmp_path / "made")

    async def run() -> None:
        async with FakeHawk() as hawk:
            runner_hook(monkeypatch, hawk, tmp_path)
            monkeypatch.setenv("HAWK_API_URL", hawk.url)
            run_id = await HawkRuns(hawk.url, credential_mode="restricted").submit(JOB)
            hawk.finish(run_id, logs=[log])
            # fetch_logs is synchronous: off this loop, which serves the fake
            fetched = await asyncio.to_thread(fetch_logs, f"hawk:{run_id}")
            assert (Path(fetched) / run_id / log.name).read_bytes() == log.read_bytes()
            assert set(hawk.tokens_seen[-2:]) == {"Bearer access-1"}

    asyncio.run(run())


def test_a_child_names_the_audit_that_launched_it(monkeypatch: pytest.MonkeyPatch) -> None:
    """On a Hawk runner the audit's own job id travels with every child, so its jobs can
    be told from another audit's in Hawk's list; Hawk accepts the config."""
    from hawk.core.types import EvalSetConfig

    from inspect_audit.dispatch import hawk_config

    monkeypatch.delenv("HAWK_JOB_ID", raising=False)
    assert "tags" not in hawk_config(JOB)
    monkeypatch.setenv("HAWK_JOB_ID", "ia-br-gsm8k-abc")
    config = hawk_config(JOB)
    assert config["tags"] == ["audit:ia-br-gsm8k-abc"]
    assert config["metadata"] == {"launched_by": "ia-br-gsm8k-abc"}
    EvalSetConfig.model_validate(config)


def test_a_judge_the_benchmarks_arguments_name_is_declared_for_every_job_kind() -> None:
    """Breadth round, writingbench: its judge is the task argument `judge_model`; jobs
    that did not declare it got no credentials for it and failed to load (FRICTION 143)."""
    from hawk.core.types import EvalSetConfig

    from inspect_audit.dispatch import hawk_config

    def declared(job: Job) -> list[str]:
        config = hawk_config(job)
        EvalSetConfig.model_validate(config)
        return [m["name"] for g in config.get("additional_models") or [] for m in g["items"]]

    args = {"judge_model": "openai/gpt-6-luna", "domain1": None}
    benchmark = Job(label="b", task="pkg/task", package="pkg", models=["m/x"], task_args=args)
    assert declared(benchmark) == ["openai/gpt-6-luna"]
    variant = Job(
        label="v",
        task="inspect_audit/variant",
        package="inspect_audit",
        models=["m/x"],
        task_args={"task": "pkg/task", "task_args": args, "files": {}, "source": "pkg.t"},
    )
    assert declared(variant) == ["openai/gpt-6-luna"]
    assert declared(JOB) == []  # nothing named, nothing declared
