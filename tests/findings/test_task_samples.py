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
        "scorers": [],
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


RICH_TASK_FILE = """
import numpy as np
from inspect_ai import Task, task
from inspect_ai.dataset import MemoryDataset, Sample
from inspect_ai.model import ChatMessageSystem, ChatMessageUser, ContentImage, ContentText


class Opaque:
    def __str__(self) -> str:
        return "opaque"


@task
def rich() -> Task:
    return Task(
        dataset=MemoryDataset(
            [
                Sample(
                    id="img-1",
                    input=[
                        ChatMessageSystem(content="Answer briefly."),
                        ChatMessageUser(
                            content=[
                                ContentText(text="What colour is this?"),
                                ContentImage(image="data:image/png;base64,iVBORw0KGgo="),
                            ]
                        ),
                    ],
                    target=["red", "crimson"],
                    choices=["red", "blue"],
                    files={"notes.txt": "hello"},
                    sandbox="docker",
                    setup="echo ready",
                    metadata={"count": np.int64(3), "thing": Opaque(), "subject": "art"},
                )
            ]
        )
    )
"""


def test_dump_and_replay_keep_messages_images_files_and_sandbox(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "rich_task.py").write_text(RICH_TASK_FILE)
    samples, meta = tmp_path / "samples.jsonl", tmp_path / "meta.json"
    _dump_task_samples.main(["rich_task.py@rich", str(samples), str(meta)])
    monkeypatch.setenv(_replay_samples.SAMPLES_ENV, str(samples))
    (replayed,) = list(_replay_samples.replay_samples().dataset)
    assert replayed.model_dump(exclude={"metadata"}) == {
        **replayed.model_dump(exclude={"metadata"}),
        "id": "img-1",
        "target": ["red", "crimson"],
        "choices": ["red", "blue"],
        "files": {"notes.txt": "hello"},
        "setup": "echo ready",
    }
    assert replayed.sandbox is not None and replayed.sandbox.type == "docker"
    assert isinstance(replayed.input, list) and [m.role for m in replayed.input] == [
        "system",
        "user",
    ]
    content = replayed.input[1].content
    assert isinstance(content, list) and content[1].type == "image"
    # values JSON cannot hold are written as their str() rather than failing the whole eval
    assert replayed.metadata == {"count": 3, "thing": "opaque", "subject": "art"}


SCORED_TASK_FILE = TASK_FILE.replace(
    "from inspect_ai import Task, task",
    "from inspect_ai import Task, task\nfrom inspect_ai.scorer import exact, includes",
).replace("        )\n    )\n", "        ),\n        scorer=[exact(), includes()],\n    )\n")


def test_dump_records_the_scorers_and_the_replay_declares_them(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from inspect_ai._util.registry import registry_info

    task_file = tmp_path / "scored_task.py"
    task_file.write_text(SCORED_TASK_FILE)
    samples, meta = tmp_path / "samples.jsonl", tmp_path / "meta.json"
    _dump_task_samples.main([f"{task_file}@tiny", str(samples), str(meta)])
    assert json.loads(meta.read_text())["scorers"] == ["inspect_ai/exact", "inspect_ai/includes"]
    monkeypatch.setenv(_replay_samples.SAMPLES_ENV, str(samples))
    monkeypatch.setenv(_replay_samples.SCORERS_ENV, "inspect_ai/exact,inspect_evals/custom")
    replayed = _replay_samples.replay_samples()
    assert isinstance(replayed.scorer, list)
    assert [registry_info(s).name for s in replayed.scorer] == [
        "inspect_ai/exact",
        "inspect_evals/custom",
    ]


def test_replay_without_scorers_declares_none(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "samples.jsonl").write_text(Sample(id="a", input="?", target="A").model_dump_json())
    monkeypatch.setenv(_replay_samples.SAMPLES_ENV, str(tmp_path / "samples.jsonl"))
    monkeypatch.delenv(_replay_samples.SCORERS_ENV, raising=False)
    assert _replay_samples.replay_samples().scorer is None


MODEL_TASK_FILE = """
from inspect_ai import Task, task
from inspect_ai.dataset import MemoryDataset, Sample
from inspect_ai.model import get_model


@task
def graded() -> Task:
    grader = get_model()
    other = get_model("nosuchprovider/judge")
    return Task(
        dataset=MemoryDataset([Sample(id="a", input=grader.name + " " + other.name)]),
    )
"""


def test_dump_gives_a_task_that_resolves_models_while_it_is_built_a_stand_in(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("INSPECT_EVAL_MODEL", raising=False)
    task_file = tmp_path / "graded_task.py"
    task_file.write_text(MODEL_TASK_FILE)
    samples, meta = tmp_path / "samples.jsonl", tmp_path / "meta.json"
    _dump_task_samples.main([f"{task_file}@graded", str(samples), str(meta)])
    (line,) = samples.read_text().splitlines()
    assert Sample.model_validate_json(line).input == "model model"


def test_dump_leaves_get_model_as_it_found_it(tmp_path: Path) -> None:
    import inspect_ai.model
    import inspect_ai.model._model

    before = (inspect_ai.model.get_model, inspect_ai.model._model.get_model)
    task_file = tmp_path / "tiny_task.py"
    task_file.write_text(TASK_FILE)
    _dump_task_samples.main([f"{task_file}@tiny", str(tmp_path / "s"), str(tmp_path / "m")])
    assert (inspect_ai.model.get_model, inspect_ai.model._model.get_model) == before
