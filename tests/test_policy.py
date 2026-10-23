"""The launch policy: what a declaration means, and what the authoriser lets through."""

import pytest
from inspect_ai.tool import ToolError

from inspect_audit.dispatch import (
    HawkRuns,
    Job,
    hawk_config,
    hawk_supports_restricted,
    validate_environment,
)
from inspect_audit.dispatch.policy import AuditAuthorizer, Execution, LaunchPolicy, Models


def test_execution_declares_its_credentials_by_backend() -> None:
    assert Execution.model_validate({"backend": "local"}).credential_mode == "user"
    assert Execution.model_validate({"backend": "hawk"}).credential_mode == "restricted"
    with pytest.raises(ValueError, match="restricted credentials require Hawk"):
        Execution.model_validate({"backend": "local", "credential_mode": "restricted"})


@pytest.mark.skipif(hawk_supports_restricted(), reason="this Hawk has restricted credentials")
def test_restricted_mode_on_a_hawk_without_it_is_refused_at_construction() -> None:
    with pytest.raises(RuntimeError, match="credential_mode: user"):
        HawkRuns("https://hawk.test", credential_mode="restricted")


def _authoriser() -> AuditAuthorizer:
    return AuditAuthorizer(
        LaunchPolicy(
            task="bench",
            task_package="bench-pkg",
            investigation_package="investigation-pkg",
            investigator_image="img",
            models=Models(subjects=("m",)),
            checks=("c",),
            configuration_json="{}",
        )
    )


@pytest.mark.parametrize("label", ["../escape", "a/b", "", "x" * 49, "-lead", "has space"])
def test_a_label_that_is_not_a_plain_name_is_refused(label: str) -> None:
    job = Job(label=label, task="bench", package="bench-pkg", models=["m"])
    with pytest.raises(ToolError, match="label"):
        _authoriser().authorise(job, "benchmark", set())


def test_a_plain_label_is_authorised() -> None:
    job = Job(label="smoke_1-a", task="bench", package="bench-pkg", models=["m"])
    assert _authoriser().authorise(job, "benchmark", set()).label == "smoke_1-a"


def test_declared_environment_is_frozen_and_applied_to_child_configs() -> None:
    authoriser = _authoriser()
    policy = authoriser.policy.model_copy(
        update={"environment_json": '{"MC_IMAGE_NAME":"ghcr.io/jrh-mann/mirrorcode"}'}
    )
    job = Job(label="probe", task="bench", package="bench-pkg", models=["m"])
    approved = AuditAuthorizer(policy).authorise(job, "benchmark", set())
    assert approved.environment == {"MC_IMAGE_NAME": "ghcr.io/jrh-mann/mirrorcode"}
    assert hawk_config(approved)["runner"]["environment"] == approved.environment
    approved.environment["MC_IMAGE_NAME"] = "changed"
    assert "changed" not in policy.environment_json
    job.environment = {"MC_IMAGE_NAME": "changed"}
    with pytest.raises(ToolError, match="environment must match"):
        AuditAuthorizer(policy).authorise(job, "benchmark", set())


@pytest.mark.parametrize(
    "environment", [{"bad-name": "x"}, {"X": 1}, {"X": "a\x00b"}, {"HAWK_API_URL": "x"}]
)
def test_invalid_runner_settings_are_refused(environment) -> None:
    with pytest.raises(ValueError):
        validate_environment(environment)
