"""A restricted Hawk runner reads only the logs its config declares, for every job that reads logs."""

import pytest

from inspect_audit.dispatch.hawk import hawk_config
from inspect_audit.dispatch.jobs import Job, JobKind


def job(kind: JobKind, logs: list[str]) -> Job:
    return Job(
        label="j",
        task=str(kind.task or "b/t"),
        package="p",
        models=["m"],
        task_args={"logs": logs} if logs else {},
    )


@pytest.mark.parametrize("kind", [JobKind.INVESTIGATION, JobKind.REGRADE])
def test_jobs_that_read_logs_declare_them(kind: JobKind) -> None:
    """Smoke round of 17 Nov: restricted regrades declared nothing and failed to load,
    asking for a user login a restricted runner does not have."""
    config = hawk_config(job(kind, ["hawk:a", "hawk:b", "hawk:a"]), restricted=True)
    assert config["transcripts"] == {"sources": [{"eval_set_id": "a"}, {"eval_set_id": "b"}]}


def test_user_credential_jobs_declare_nothing() -> None:
    assert "transcripts" not in hawk_config(job(JobKind.REGRADE, ["hawk:a"]))


def test_a_restricted_job_cannot_read_a_log_hawk_does_not_hold() -> None:
    with pytest.raises(ValueError, match="require Hawk transcript sources"):
        hawk_config(job(JobKind.REGRADE, ["/tmp/x.eval"]), restricted=True)
