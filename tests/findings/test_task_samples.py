"""The two scripts a task scan runs: dump a task's samples in the eval's environment, replay them."""

import json
from pathlib import Path

import pytest
from inspect_ai.dataset import Sample

from inspect_audit.findings.adapters import _dump_task_samples, _replay_samples

TASK_FILE = """
from inspect_ai import Task, task
from inspect_ai.dataset import MemoryDataset, Sample


@task
def tiny() -> Task:
    return Task(
        dataset=MemoryDataset(
            [
                Sample(id="a", input="Is it red or blue?", target="A", choices=["red", "blue"]),
                Sample(id=2, input="Two?", target=["2", "two"], metadata={"subject": "maths"}),
            ],
            name="owner/tiny",
            location="owner/tiny",
        )
    )
"""


def test_dump_writes_every_sample_and_the_dataset_identity(tmp_path: Path) -> None:
    task_file = tmp_path / "tiny_task.py"
    task_file.write_text(TASK_FILE)
    samples, meta = tmp_path / "samples.jsonl", tmp_path / "meta.json"
    _dump_task_samples.main([f"{task_file}@tiny", str(samples), str(meta)])
    lines = samples.read_text().splitlines()
    assert [Sample.model_validate_json(line).id for line in lines] == ["a", 2]
    assert json.loads(meta.read_text()) == {
        "task": f"{task_file}@tiny",
        "dataset_name": "owner/tiny",
        "dataset_location": "owner/tiny",
        "samples": 2,
    }


def test_dump_refuses_a_spec_matching_several_tasks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(
        tmp_path
    )  # inspect's loader globs file specs relative to the working directory
    task_file = tmp_path / "two_tasks.py"
    task_file.write_text(TASK_FILE + "\n\n@task\ndef other() -> Task:\n    return tiny()\n")
    with pytest.raises(SystemExit, match="2 tasks"):
        _dump_task_samples.main(["two_tasks.py", str(tmp_path / "s"), str(tmp_path / "m")])


def test_replay_returns_the_dumped_samples_unchanged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = [
        Sample(id="a", input="Is it red or blue?", target="A", choices=["red", "blue"]),
        Sample(id=2, input="Two?", target=["2", "two"], metadata={"subject": "maths"}),
    ]
    samples = tmp_path / "samples.jsonl"
    samples.write_text("".join(s.model_dump_json() + "\n" for s in original))
    monkeypatch.setenv(_replay_samples.SAMPLES_ENV, str(samples))
    replayed = list(_replay_samples.replay_samples().dataset)
    assert [s.model_dump() for s in replayed] == [s.model_dump() for s in original]
