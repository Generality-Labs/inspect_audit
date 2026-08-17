"""The audit filesystem materialised for a case.

Uses a real `Task` and a real scorer rather than fixtures that resolve a published
benchmark, so the tests need no network and run in milliseconds.
"""

import json
from pathlib import Path

from inspect_ai import Task
from inspect_ai.dataset import MemoryDataset, Sample, json_dataset
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


def test_case_holds_the_sample_the_grading_doc_and_its_provenance(tmp_path: Path) -> None:
    task = make_task()
    files = materialise(task, task.dataset[0], make_case_spec(), stage=tmp_path)

    # No logs were given, so there are no sliced logs — and nothing else is invented.
    assert set(files) == {
        f"{AUDIT_ROOT}/sample.json",
        f"{AUDIT_ROOT}/case.json",
        f"{AUDIT_ROOT}/gold/grading.md",
    }


def test_every_value_is_a_host_path_never_contents(tmp_path: Path) -> None:
    """Contents-shaped values are ambiguous to Inspect; paths are not.

    Inspect resolves a `Sample.files` value by trying a data URI, then an HTTP GET,
    then an existing file at that path. A gold of "pyproject.toml" would therefore be
    replaced by that file's bytes, and a URL-shaped gold would be fetched from the web.
    """
    task = make_task()
    files = materialise(task, task.dataset[0], make_case_spec(), stage=tmp_path)

    for value in files.values():
        assert Path(value).is_file(), f"{value} is not a staged file"


def test_sample_is_written_in_inspects_own_shape(tmp_path: Path) -> None:
    """The sample is loadable as a dataset, not a rendering of ours."""
    task = make_task({"references": ["https://example.org/a"]})
    files = materialise(task, task.dataset[0], make_case_spec(), stage=tmp_path)

    record = json.loads(Path(files[f"{AUDIT_ROOT}/sample.json"]).read_text())
    assert record == [
        {
            "id": 863,
            "input": QUESTION,
            "target": "1915",
            "metadata": {"references": ["https://example.org/a"]},
        }
    ]

    # Inspect's own loader accepts it, so the auditor can rebuild the item exactly as
    # the benchmark defines it.
    loaded = json_dataset(files[f"{AUDIT_ROOT}/sample.json"])
    assert loaded[0].input == QUESTION
    assert loaded[0].target == "1915"
    assert (loaded[0].metadata or {})["references"] == ["https://example.org/a"]


def test_grading_doc_points_at_the_real_artefacts_rather_than_restating_them(
    tmp_path: Path,
) -> None:
    task = make_task({"note": "see https://b.example"})
    files = materialise(task, task.dataset[0], make_case_spec(), stage=tmp_path)
    grading = Path(files[f"{AUDIT_ROOT}/gold/grading.md"]).read_text()

    # Where the code lives: a task's scorer routinely delegates outside its own package.
    assert "inspect_ai.scorer" in grading
    assert "importlib" in grading
    # How to read the logs, using Inspect's API rather than a schema of ours.
    assert "read_eval_log" in grading
    assert "samples_df" in grading
    # Metadata keys are surfaced so an auditor can spot reference material we cannot name.
    assert "`note`" in grading
    # And the caveat that `target` may not be the whole gold.
    assert "whole gold" in grading


def test_case_sample_carries_the_case_spec_and_the_files(tmp_path: Path) -> None:
    task = make_task()
    case = case_sample(
        task,
        task.dataset[0],
        make_case_spec(),
        prompt="audit it",
        stage=tmp_path,
        sandbox="docker",
    )

    assert case.id == "863"
    assert case.input == "audit it"
    assert case.target == "1915"
    assert case.files is not None and f"{AUDIT_ROOT}/case.json" in case.files
    assert (case.metadata or {})["case_spec"]["sample_id"] == 863
