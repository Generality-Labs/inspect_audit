"""The audit filesystem materialised for a case.

Uses a real `Task` and a real scorer rather than fixtures that resolve a published
benchmark, so the tests need no network and run in milliseconds.
"""

import json

import pytest
from inspect_ai import Task
from inspect_ai.dataset import MemoryDataset, Sample
from inspect_ai.scorer import match

from inspect_audit import CaseSpec
from inspect_audit._case import AUDIT_ROOT, case_sample, materialise

QUESTION = "In what year did Aleksandrov prove his first important result?"


def make_task(metadata: dict[str, object] | None = None) -> Task:
    return Task(
        name="fixture_task",
        dataset=MemoryDataset([Sample(id=863, input=QUESTION, target="1915", metadata=metadata)]),
        scorer=match(),
    )


def make_case_spec() -> CaseSpec:
    return CaseSpec(task="fixture_task", sample_id=863, input_hash="deadbeef")


def test_materialise_writes_the_bounded_case_files() -> None:
    task = make_task()
    files = materialise(task, task.dataset[0], make_case_spec())

    assert set(files) == {
        f"{AUDIT_ROOT}/sample.json",
        f"{AUDIT_ROOT}/prompt.txt",
        f"{AUDIT_ROOT}/case.json",
        f"{AUDIT_ROOT}/gold/target.txt",
        f"{AUDIT_ROOT}/gold/grading.md",
        f"{AUDIT_ROOT}/elicitation.json",
    }
    assert files[f"{AUDIT_ROOT}/prompt.txt"] == QUESTION
    assert files[f"{AUDIT_ROOT}/gold/target.txt"] == "1915"
    assert json.loads(files[f"{AUDIT_ROOT}/sample.json"])["sample_id"] == 863
    assert all(isinstance(v, str) for v in files.values())


def test_grading_doc_points_at_the_module_rather_than_copying_it() -> None:
    task = make_task()
    grading = materialise(task, task.dataset[0], make_case_spec())[f"{AUDIT_ROOT}/gold/grading.md"]

    # A task's scorer routinely delegates outside its own package, which is why we
    # name where the source lives instead of copying it.
    assert "inspect_ai.scorer" in grading
    assert "importlib" in grading


def test_cited_sources_are_found_anywhere_in_metadata_not_under_a_known_key() -> None:
    # Deliberately not keyed on `urls`: no two benchmarks name it the same way.
    task = make_task({"references": ["https://example.org/a"], "note": "see https://b.example"})
    files = materialise(task, task.dataset[0], make_case_spec())

    sources = files[f"{AUDIT_ROOT}/gold/sources.md"]
    assert "https://example.org/a" in sources
    assert "https://b.example" in sources
    # Metadata keys are surfaced so an auditor can spot reference material we cannot name.
    grading = files[f"{AUDIT_ROOT}/gold/grading.md"]
    assert "`note`" in grading and "`references`" in grading


def test_sources_omitted_when_the_benchmark_cites_none() -> None:
    files = materialise(make_task(), make_task().dataset[0], make_case_spec())
    assert f"{AUDIT_ROOT}/gold/sources.md" not in files


@pytest.mark.parametrize("urls", ["https://a.example,https://b.example", ["https://a.example"]])
def test_cited_sources_accept_a_string_or_a_list(urls: object) -> None:
    task = make_task({"urls": urls})
    files = materialise(task, task.dataset[0], make_case_spec())
    assert "https://a.example" in files[f"{AUDIT_ROOT}/gold/sources.md"]


def test_grading_doc_explains_the_scorer_and_what_counts_as_a_pass() -> None:
    task = make_task()
    grading = materialise(task, task.dataset[0], make_case_spec())[f"{AUDIT_ROOT}/gold/grading.md"]

    assert "inspect_ai.scorer" in grading          # where the code lives
    assert "Verdict values" in grading             # what it can return
    assert "whole gold" in grading  # the caveat that target may not be all of it


def test_case_sample_carries_the_case_spec_and_the_files() -> None:
    task = make_task()
    case = case_sample(task, task.dataset[0], make_case_spec(), prompt="audit it", sandbox="docker")

    assert case.id == "863"
    assert case.input == "audit it"
    assert case.target == "1915"
    assert case.files is not None and f"{AUDIT_ROOT}/case.json" in case.files
    assert (case.metadata or {})["case_spec"]["sample_id"] == 863
