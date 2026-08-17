"""Assembling the audit task, and resolving what is being audited.

No network and no Docker: `audit_task` accepts an already-constructed `Task`, so
these exercise the assembly without resolving a published benchmark.
"""

from pathlib import Path

import pytest
from inspect_ai import Task
from inspect_ai.dataset import MemoryDataset, Sample
from inspect_ai.scorer import match

from inspect_audit import audit_task, resolve_task
from inspect_audit._case import AUDIT_ROOT


def make_task(n: int = 3, with_ids: bool = True) -> Task:
    samples = [
        Sample(id=(100 + i) if with_ids else None, input=f"question {i}", target=str(i))
        for i in range(n)
    ]
    return Task(name="fixture_task", dataset=MemoryDataset(samples), scorer=match())


def test_resolve_task_passes_a_task_through() -> None:
    task = make_task()
    assert resolve_task(task) is task


def test_resolve_task_names_the_spec_it_could_not_resolve() -> None:
    with pytest.raises(ValueError, match="no_such_package/no_such_task"):
        resolve_task("no_such_package/no_such_task")


def test_audit_task_makes_one_case_per_sample() -> None:
    audit = audit_task(make_task(3))

    assert audit.name == "audit/fixture_task"
    assert len(audit.dataset) == 3
    assert audit.metadata is not None and audit.metadata["audited_task"] == "fixture_task"


def test_limit_and_sample_selection() -> None:
    assert len(audit_task(make_task(5), limit=2).dataset) == 2

    selected = audit_task(make_task(5), samples=[101, 103]).dataset
    assert [s.id for s in selected] == ["101", "103"]


def test_samples_without_ids_are_addressed_by_position() -> None:
    # Inspect assigns 1-based ids when a dataset does not set them, so an audit
    # must address them the same way or the join to logs silently mismatches.
    audit = audit_task(make_task(3, with_ids=False))
    assert [s.id for s in audit.dataset] == ["1", "2", "3"]


def test_each_case_gets_a_sandbox_and_a_materialised_filesystem() -> None:
    case = audit_task(make_task(1)).dataset[0]

    assert case.sandbox is not None
    assert case.files is not None
    assert f"{AUDIT_ROOT}/sample.json" in case.files


def test_the_audited_tasks_own_sandbox_wins_over_ours() -> None:
    # Inspect normalises a sandbox to a SandboxEnvironmentSpec on assignment, so
    # compare the resolved parts rather than the tuple that was passed in.
    task = make_task(1)
    task.sandbox = ("docker", "their-compose.yaml")  # type: ignore[assignment]

    theirs = audit_task(task).dataset[0].sandbox
    assert theirs is not None
    assert (theirs.type, theirs.config) == ("docker", "their-compose.yaml")

    ours = audit_task(make_task(1)).dataset[0].sandbox
    assert ours is not None
    assert ours.type == "docker"
    assert ours.config is not None and Path(str(ours.config)).name == "Dockerfile"


def test_the_case_spec_records_provenance_for_every_case() -> None:
    for case in audit_task(make_task(2)).dataset:
        case_spec = (case.metadata or {})["case_spec"]
        assert case_spec["task"] == "fixture_task"
        assert case_spec["input_hash"]
        assert case_spec["attempts"] == []  # attempts arrive with candidates()


def test_the_generated_sandbox_pins_the_tasks_own_packages() -> None:
    from inspect_audit._sandbox import task_requirements

    reqs = task_requirements(make_task(1))
    # inspect-ai always, pinned to the resolving environment's version.
    assert any(r.startswith("inspect-ai==") for r in reqs)
    assert all("==" in r for r in reqs)
