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


def test_grading_doc_import_line_handles_multiple_scorer_modules(tmp_path: Path) -> None:
    """The module-source snippet must stay runnable when scorers span two packages.

    A task whose scorers come from different modules space-joins them into the
    `{modules}` slot; the old snippet fed that straight to a single
    `import_module(...)`, which is broken Python. It must iterate instead.
    """
    from inspect_ai.scorer import Score, Scorer, Target, accuracy, match, scorer
    from inspect_ai.solver import TaskState

    # a scorer defined here, so its module differs from match()'s -- the case the
    # bug hit: two scorer packages space-joined into one import statement
    @scorer(metrics=[accuracy()])
    def local_scorer() -> Scorer:
        async def score(state: TaskState, target: Target) -> Score:
            return Score(value="C")

        return score

    task = Task(
        name="two_scorer_task",
        dataset=MemoryDataset([Sample(id=1, input=QUESTION, target="1915")]),
        scorer=[match(), local_scorer()],
    )
    files = item_files(task, task.dataset[0], [], stage=tmp_path)
    grading = Path(files[f"{AUDIT_ROOT}/gold/grading.md"]).read_text()

    # the doc names both distinct modules
    assert "inspect_ai.scorer" in grading
    assert __name__ in grading

    line = next(ln for ln in grading.splitlines() if "getsourcefile" in ln)
    # the snippet splits the module list and imports each, rather than importing
    # one module named "mod_a mod_b"
    assert ".split()" in line
    body = line.split('python -c "', 1)[1].rsplit('"', 1)[0]
    compile(body, "<grading-snippet>", "exec")  # would raise if it were broken Python


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


def test_redact_strips_extra_metadata_keys_and_their_names(tmp_path: Path) -> None:
    """A key that pre-empts the finding under audit is withheld, name included.

    Integrity Bench records the construction-validator vote pattern that *is* the
    difficulty label under audit; an auditor that reads it is no longer an
    independent witness. Naming the key alone leaks the finding, so `grading.md`
    must not list it either.
    """
    task = make_task({"answer": "1915", "gemini_vote_pattern": "1/3", "kind": "spatial"})
    files = item_files(
        task, task.dataset[0], [], stage=tmp_path, redact=("gemini_vote_pattern",)
    )

    staged = json.loads(Path(files[f"{AUDIT_ROOT}/sample.json"]).read_text())[0]
    assert "gemini_vote_pattern" not in staged["metadata"]
    assert staged["metadata"]["kind"] == "spatial"

    grading = Path(files[f"{AUDIT_ROOT}/gold/grading.md"]).read_text()
    assert "gemini_vote_pattern" not in grading
    assert "`kind`" in grading


def test_benchmark_metadata_is_carried_without_a_benchmark_container(tmp_path: Path) -> None:
    """`grade` reads the benchmark's own metadata, and a sandboxless task still has one.

    A text benchmark declares no sandbox, so `benchmark=False`, but its scorer still
    reads `metadata` for the recorded answer. Withholding it there left `grade` -- and
    so the whole red-teaming item -- broken on every task without a container.
    """
    task = make_task({"answer": "1915"})
    item = AuditItem(task=task.name, sample_id=863)
    sample = item_sample(
        task, task.dataset[0], item, prompt="p", stage=tmp_path, benchmark=False
    )

    assert (sample.metadata or {})["benchmark_metadata"] == {"answer": "1915"}


def test_redaction_does_not_reach_the_grader(tmp_path: Path) -> None:
    """Redaction blinds the auditor, never the scorer that has to grade against it."""
    task = make_task({"answer": "1915", "gemini_vote_pattern": "1/3"})
    item = AuditItem(task=task.name, sample_id=863)
    sample = item_sample(
        task,
        task.dataset[0],
        item,
        prompt="p",
        stage=tmp_path,
        redact=("gemini_vote_pattern", "answer"),
    )

    assert (sample.metadata or {})["benchmark_metadata"] == {
        "answer": "1915",
        "gemini_vote_pattern": "1/3",
    }


def test_benchmark_code_is_staged_so_the_auditor_can_read_the_grader(tmp_path: Path) -> None:
    """`gold/grading.md` tells the auditor to read the real grading code.

    `task_requirements` only pins distributions, so a benchmark that is a loose
    repository rather than a published package installs nothing into the auditor's
    box and that instruction fails. Stage the scorer's own source instead.
    """
    task = make_task()
    files = item_files(task, task.dataset[0], [], stage=tmp_path)

    staged = {k for k in files if k.startswith(f"{AUDIT_ROOT}/benchmark/")}
    assert staged, "no benchmark source staged"
    # `match()` is scored by inspect_ai's own module, which is what this fixture uses
    assert any(Path(files[k]).read_text().strip() for k in staged)


def test_benchmark_staging_excludes_data_and_logs(tmp_path: Path) -> None:
    """A benchmark's data directory is routinely large enough to swamp the cell."""
    from inspect_audit._item import benchmark_source_files

    sources = benchmark_source_files(make_task())
    assert all(p.suffix == ".py" for p in sources.values())
    assert not any("/data/" in str(p) or "/logs/" in str(p) for p in sources.values())


def test_item_media_is_copied_into_the_cell_and_rewritten(tmp_path: Path) -> None:
    """An image referenced by host path is dead inside the auditor's container.

    Inspect resolves dataset media against the machine that built the dataset. Staged
    verbatim, a vision item asks the auditor to check a chair count against a path
    that does not exist, so the item is audited blind.
    """
    from inspect_ai.model import ChatMessageUser
    from inspect_ai.tool import ContentImage, ContentText

    png = tmp_path / "view_0.png"
    png.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 32)

    task = Task(
        name="fixture_vision",
        dataset=MemoryDataset(
            [
                Sample(
                    id=1,
                    input=[
                        ChatMessageUser(
                            content=[
                                ContentText(text="count the chairs"),
                                ContentImage(image=str(png)),
                            ]
                        )
                    ],
                    target="3",
                )
            ]
        ),
        scorer=match(),
    )
    files = item_files(task, task.dataset[0], [], stage=tmp_path / "stage")

    staged = [k for k in files if k.startswith(f"{AUDIT_ROOT}/media/")]
    assert len(staged) == 1
    assert Path(files[staged[0]]).read_bytes().startswith(b"\x89PNG")

    record = json.loads(Path(files[f"{AUDIT_ROOT}/sample.json"]).read_text())[0]
    refs = [
        c["image"]
        for m in record["input"]
        for c in m["content"]
        if isinstance(c, dict) and c.get("type") == "image"
    ]
    assert refs == staged, "sample.json still points at the host path"


def test_media_staging_leaves_uris_alone(tmp_path: Path) -> None:
    """A data: or http URI needs no staging and must not be mangled."""
    from inspect_audit._item import media_files

    record = {
        "input": [
            {"content": [{"type": "image", "image": "data:image/png;base64,AAAA"}]},
            {"content": [{"type": "image", "image": "https://example.org/a.png"}]},
        ]
    }
    assert media_files(record, stage=tmp_path) == {}
    assert record["input"][0]["content"][0]["image"].startswith("data:")
    assert record["input"][1]["content"][0]["image"].startswith("https://")
