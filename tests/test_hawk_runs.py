"""`HawkRuns` against a Hawk API on localhost, through Hawk's own client.

Nothing of ours is patched: the real submission, status, usage, log download and stop
paths make real HTTP requests, and the fake answers with Hawk's own response models.
"""

import asyncio
from pathlib import Path

import pytest
from inspect_ai import Task, eval
from inspect_ai.dataset import Sample
from test_helpers.fake_hawk import FakeHawk

from inspect_audit.dispatch import HawkRuns, Job, JobState
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
            hawk.progress(
                run_id,
                {"openai/x": {"input_tokens": 100, "output_tokens": 20, "total_tokens": 120}},
            )
            usage, done = await runs.usage(run_id)
            assert usage["openai/x"].input_tokens == 100 and done == 0

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
