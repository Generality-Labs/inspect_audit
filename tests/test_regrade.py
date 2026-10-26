"""Standalone regrading: Inspect's own rescoring of real logs, diffed against the record."""

import random
from pathlib import Path

from inspect_ai import Task
from inspect_ai.dataset import MemoryDataset, Sample
from inspect_ai.log import read_eval_log, write_eval_log
from inspect_ai.scorer import Score, Target, accuracy, scorer
from inspect_ai.solver import TaskState
from inspect_ai.util import sandbox
from test_helpers.logs import fixture_task

from inspect_audit._regrade import AGREE, NEEDS_BOX, NOISY, STABLE, regrade


def test_a_faithful_grader_agrees_with_every_recorded_grade(graded_log: str) -> None:
    rows = regrade(fixture_task("graded_task"), logs=[graded_log])
    assert [(r.sample_id, r.recorded, r.status) for r in rows] == [
        ("1", "C", AGREE),
        ("2", "I", AGREE),
        ("3", "C", AGREE),
    ]


def test_a_tampered_grade_is_a_stable_disagreement(graded_log: str, tmp_path: Path) -> None:
    log = read_eval_log(graded_log)
    assert log.samples is not None and log.samples[1].scores is not None
    log.samples[1].scores["match"].value = "C"  # the record says C; the grader says I
    tampered = str(tmp_path / "tampered.eval")
    write_eval_log(log, tampered)
    rows = regrade(fixture_task("graded_task"), logs=[tampered])
    assert {r.sample_id: r.status for r in rows} == {"1": AGREE, "2": STABLE, "3": AGREE}


@scorer(metrics=[accuracy()], name="match")
def coin() -> object:
    async def score(state: TaskState, target: Target) -> Score:
        return Score(value=random.choice("ABDEFGH"))  # never C or I: always disagrees

    return score


def test_a_grader_that_varies_is_noise_not_a_fault(graded_log: str) -> None:
    noisy = Task(
        name="graded_task",
        dataset=MemoryDataset([Sample(id=i, input=f"q{i}", target="ANSWER") for i in (1, 2, 3)]),
        scorer=coin(),
    )
    rows = regrade(noisy, logs=[graded_log], resamples=5)
    assert all(r.status == NOISY for r in rows)


@scorer(metrics=[accuracy()], name="match")
def needs_box() -> object:
    async def score(state: TaskState, target: Target) -> Score:
        await sandbox().exec(["true"])
        return Score(value="C")

    return score


def test_a_scorer_that_needs_the_box_is_reported_not_worked_around(graded_log: str) -> None:
    boxed = Task(
        name="graded_task",
        dataset=MemoryDataset([Sample(id=1, input="q", target="ANSWER")]),
        scorer=needs_box(),
    )
    rows = regrade(boxed, logs=[graded_log])
    assert rows and all(r.status == NEEDS_BOX for r in rows), rows


def test_a_scorer_that_returns_nothing_does_not_shift_the_others() -> None:
    """Pairing by position filed every later score under the wrong scorer (FRICTION 4)."""
    from inspect_audit._regrade import _fresh

    original = {"judge": 0, "exact": 0}
    rescored = {**original, "exact1": 1}  # the judge returned None this time
    assert _fresh(original, rescored, ["judge", "exact"]) == [("judge", None), ("exact", "exact1")]


def test_a_regrade_uses_the_judges_recorded_settings() -> None:
    """With defaults instead, a regrade tests a different grader (FRICTION 8)."""
    from inspect_ai.model import GenerateConfig, Model, ModelConfig

    from inspect_audit._logs import LogIndex
    from inspect_audit._task import configured_judges

    recorded = ModelConfig(model="mockllm/model", config=GenerateConfig(temperature=0.3))
    index = LogIndex(attempts={}, judge_configs={"grader": recorded})
    [judge] = configured_judges(index, {"grader": "mockllm/model"}).values()
    assert isinstance(judge, Model) and judge.config.temperature == 0.3


def test_a_regrade_job_replays_each_attempt_and_scores_it_each_epoch(
    graded_log: str, tmp_path: Path
) -> None:
    """The regrade the auditor launches as a job: no model call, the recorded answer
    scored again by the benchmark's own scorer, once per epoch (FRICTION 20)."""
    from inspect_ai import eval as inspect_eval

    from inspect_audit._regrade import REPLAY, regrade_task

    task = regrade_task(
        fixture_task("graded_task"), logs=[graded_log], samples=["1", "2"], epochs=2
    )
    [log] = inspect_eval(task, model="mockllm/model", log_dir=str(tmp_path), display="none")
    assert log.status == "success", log.error
    grades = {
        (s.metadata[REPLAY]["sample_id"], s.epoch): (s.scores or {})["match"].value
        for s in log.samples or []
    }
    # sample 3 not selected; each selected attempt scored twice, matching its record
    assert grades == {("1", 1): "C", ("1", 2): "C", ("2", 1): "I", ("2", 2): "I"}
    recorded = {
        s.metadata[REPLAY]["sample_id"]: s.metadata[REPLAY]["recorded"] for s in log.samples or []
    }
    assert recorded == {"1": {"match": "C"}, "2": {"match": "I"}}
    calls = [e for s in log.samples or [] for e in s.events if e.event == "model"]
    assert not calls  # a replay, not a rerun
