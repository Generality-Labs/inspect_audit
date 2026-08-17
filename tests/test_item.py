"""The filesystem an auditor gets for one item.

Uses a real `Task` and a real scorer rather than fixtures that resolve a published
benchmark, so the tests need no network and run in milliseconds.
"""

import json
from pathlib import Path

from inspect_ai import Task
from inspect_ai.dataset import MemoryDataset, Sample, json_dataset
from inspect_ai.scorer import match

from inspect_audit import AuditItem
from inspect_audit._item import AUDIT_ROOT, item_files, item_sample

QUESTION = "In what year did Aleksandrov prove his first important result?"


def make_task(metadata: dict[str, object] | None = None) -> Task:
    return Task(
        name="fixture_task",
        dataset=MemoryDataset([Sample(id=863, input=QUESTION, target="1915", metadata=metadata)]),
        scorer=match(),
    )


def test_every_value_is_a_host_path_never_contents(tmp_path: Path) -> None:
    """Contents-shaped values are ambiguous to Inspect; paths are not.

    Inspect resolves a `Sample.files` value as a data URI, then an HTTP GET, then an
    existing file at that path. A target of "pyproject.toml" would therefore be
    replaced by that file's bytes, and a URL-shaped target fetched from the web.
    """
    task = make_task()
    files = item_files(task, task.dataset[0], [], stage=tmp_path)

    assert files
    for value in files.values():
        assert Path(value).is_file(), f"{value} is not a staged file"


def test_sample_is_written_in_inspects_own_shape(tmp_path: Path) -> None:
    """The sample is loadable as a dataset, not a rendering of ours."""
    task = make_task({"references": ["https://example.org/a"]})
    files = item_files(task, task.dataset[0], [], stage=tmp_path)
    path = files[f"{AUDIT_ROOT}/sample.json"]

    assert json.loads(Path(path).read_text()) == [
        {
            "id": 863,
            "input": QUESTION,
            "target": "1915",
            "metadata": {"references": ["https://example.org/a"]},
        }
    ]

    loaded = json_dataset(path)
    assert loaded[0].input == QUESTION
    assert loaded[0].target == "1915"
    assert (loaded[0].metadata or {})["references"] == ["https://example.org/a"]


def test_grading_doc_points_at_the_real_artefacts_rather_than_restating_them(
    tmp_path: Path,
) -> None:
    task = make_task({"note": "see https://b.example"})
    files = item_files(task, task.dataset[0], [], stage=tmp_path)
    grading = Path(files[f"{AUDIT_ROOT}/gold/grading.md"]).read_text()

    # Where the code lives: a task's scorer routinely delegates outside its own package.
    assert "inspect_ai.scorer" in grading
    assert "importlib" in grading
    # How to read the logs, through Inspect's API rather than a schema of ours.
    assert "read_eval_log" in grading
    assert "samples_df" in grading
    # Metadata keys are surfaced so an auditor can spot reference material we cannot name.
    assert "`note`" in grading
    # And the caveat that `target` may not be the whole gold.
    assert "whole gold" in grading


def test_item_sample_records_which_item_it_audits(tmp_path: Path) -> None:
    task = make_task()
    audited = item_sample(
        task,
        task.dataset[0],
        AuditItem(task="fixture_task", sample_id=863),
        prompt="audit it",
        stage=tmp_path,
        sandbox="docker",
    )

    assert audited.id == "863"
    assert audited.input == "audit it"
    assert audited.target == "1915"
    assert (audited.metadata or {})["audit_item"]["sample_id"] == 863
    assert audited.files is not None and f"{AUDIT_ROOT}/sample.json" in audited.files
