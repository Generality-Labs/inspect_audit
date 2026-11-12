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


def test_declared_benchmark_pins_travel_with_every_child_job_and_nothing_else() -> None:
    """The benchmark's lockfile pins are part of the policy: present, exact, or refused."""
    from inspect_audit.dispatch.policy import AuditAuthorizer, LaunchPolicy, Models

    policy = LaunchPolicy(
        task="bench",
        task_package="bench[x] @ git+https://e/b@abc1234",
        task_pins=("swebench==4.1.0",),
        investigation_package="ia",
        investigator_image="img",
        models=Models(subjects=("m",), investigators=("i",)),
        checks=("gold-answer",),
        configuration_json="{}",
    )
    auth = AuditAuthorizer(policy)
    benchmark = Job(
        label="b",
        task="bench",
        package=policy.task_package or "",
        models=["m"],
        extra_packages=["swebench==4.1.0"],
    )
    assert auth.authorise(benchmark, "benchmark", set()).extra_packages == ["swebench==4.1.0"]
    unpinned = Job(label="b", task="bench", package=policy.task_package or "", models=["m"])
    with pytest.raises(ToolError, match="dependencies"):
        auth.authorise(unpinned, "benchmark", set())


def _valid() -> tuple["AuditAuthorizer", Job, Job]:
    """A policy, and one investigation and one benchmark job it accepts."""
    from inspect_audit.dispatch.policy import (
        AuditAuthorizer,
        LaunchPolicy,
        Limits,
        Models,
        canonical,
    )

    args = {"split": "test"}
    policy = LaunchPolicy(
        task="bench",
        task_package="bench",
        task_pins=("dep==1.0",),
        investigation_package="ia",
        investigator_image="img",
        models=Models(subjects=("subj",), investigators=("inv",), judges=("judge",)),
        samples=("1", "2"),
        checks=("gold-answer", "question-labels"),
        task_args_json=canonical(args),
        source="bench.task",
        supplied_logs=("hawk:run",),
        limits=Limits(investigation_sample_cost_usd=0.5),
        configuration_json="{}",
    )
    investigation = Job(
        label="i",
        task="inspect_audit/investigation",
        package="ia",
        models=["inv"],
        extra_packages=["bench", "dep==1.0"],
        task_args={
            "task": "bench",
            "task_args": args,
            "samples": ["1"],
            "checks": ["gold-answer"],
            "source": "bench.task",
            "logs": ["hawk:run"],
            "judge": {"grader": "judge"},
            "investigator_image": "img",
            "cost_limit": 0.2,
        },
    )
    benchmark = Job(
        label="b",
        task="bench",
        package="bench",
        models=["subj"],
        task_args=args,
        sample_ids=["2"],
        extra_packages=["dep==1.0"],
        model_roles={"grader": "judge"},
    )
    return AuditAuthorizer(policy), investigation, benchmark


def test_the_valid_jobs_pass() -> None:
    auth, investigation, benchmark = _valid()
    auth.authorise(investigation, "investigation", set())
    auth.authorise(benchmark, "benchmark", set())


@pytest.mark.parametrize(
    ("kind", "change"),
    [
        ("investigation", {"cost_limit": 0.9}),  # above the per-sample cap
        ("investigation", {"cost_limit": float("inf")}),
        ("investigation", {"cost_limit": 0}),
        ("investigation", {"logs": ["hawk:someone-elses-run"]}),
        ("investigation", {"source": "other.module"}),
        ("investigation", {"samples": ["3"]}),
        ("investigation", {"checks": ["red-teaming"]}),
        ("investigation", {"task_args": {"split": "train"}}),
        ("investigation", {"judge": {"grader": "not-allowed"}}),
        ("investigation", {"investigator_image": "evil"}),
        ("investigation", {"extra": "field"}),
        ("investigation", {"__extra_packages__": ["bench"]}),  # pins dropped
        ("benchmark", {"__sample_ids__": ["3"]}),
        ("benchmark", {"__sample_range__": (1, 1)}),  # with sample ids: one or the other
        ("benchmark", {"__models__": ["unlisted"]}),
        ("benchmark", {"__model_roles__": {"grader": "not-allowed"}}),
        ("benchmark", {"__task_args__": {"split": "train"}}),
        ("benchmark", {"__extra_packages__": []}),
    ],
)
def test_every_policy_limit_refuses_a_job_that_breaks_it(kind: str, change: dict) -> None:
    """One valid job, broken one way at a time: each must be refused."""
    import dataclasses

    auth, investigation, benchmark = _valid()
    job = investigation if kind == "investigation" else benchmark
    fields = {k.strip("_"): v for k, v in change.items() if k.startswith("__")}
    args = {k: v for k, v in change.items() if not k.startswith("__")}
    fields.setdefault("task_args", {**job.task_args, **args})
    job = dataclasses.replace(job, **fields)
    with pytest.raises(ToolError):
        auth.authorise(job, kind, set())
