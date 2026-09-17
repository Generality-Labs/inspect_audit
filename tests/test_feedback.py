"""Check feedback isolation, termination, and failures before spending on a run."""

from pathlib import Path

import pytest
from inspect_ai import Task, eval
from inspect_ai.dataset import Sample
from inspect_ai.model import ModelOutput, get_model
from inspect_ai.scorer import CORRECT, INCORRECT, NOANSWER, Score, accuracy, scorer

from inspect_audit._feedback import binary_feedback_solver, feedback_curve


def test_pilot_ids_exist_before_hawk_filters(monkeypatch) -> None:
    from inspect_ai._eval.task.util import slice_dataset
    from inspect_ai.scorer import match

    from inspect_audit import _feedback

    source = Task(
        dataset=[Sample(input=f"Question {i}", target="answer") for i in range(1000)],
        scorer=match(),
    )
    monkeypatch.setattr(_feedback, "resolve_task", lambda _: source)
    built = _feedback.binary_feedback("fixture")
    ids = [107, 592, 657, 719, 751]
    selected = slice_dataset(built.dataset, limit=None, sample_id=ids)
    assert [s.id for s in selected] == ids
    assert len(built.dataset) == 1000
    assert all(s.id is None for s in source.dataset)


@pytest.mark.parametrize(
    "grades",
    [
        [INCORRECT, CORRECT],
        [NOANSWER, INCORRECT, CORRECT],
        [CORRECT],
        [INCORRECT] * 10,
        [INCORRECT] * 100,
    ],
)
def test_feedback_and_stopping(tmp_path: Path, grades: list[str]) -> None:
    seen = []

    @scorer(metrics=[accuracy()])
    def judge():
        async def score(state, target):
            seen.append(state)
            assert state.input_text == "Original question?"
            assert target.text == "HIDDEN-GOLD"
            assert [m.text for m in state.messages] == [
                "Original question?",
                f"answer-{len(seen)}",
            ]
            assert "feedback_attempts" not in state.metadata
            return Score(value=grades[len(seen) - 1], explanation="HIDDEN-EXPLANATION")

        return score

    model = get_model(
        "mockllm/model",
        custom_outputs=[
            ModelOutput.from_content("mockllm/model", f"answer-{i}")
            for i in range(1, max(10, len(grades)) + 1)
        ],
    )
    task = Task(
        dataset=[Sample(input="Original question?", target="HIDDEN-GOLD")],
        solver=binary_feedback_solver(judge(), max(10, len(grades))),
        scorer=feedback_curve(max(10, len(grades))),
    )
    log = eval(task, model=model, display="none", log_dir=str(tmp_path))[0]
    assert log.status == "success", log.error
    sample = log.samples[0]
    assert sample.error is None
    assert len(seen) == len(grades)
    assert all("HIDDEN" not in message.text for message in sample.messages)
    feedback = [m.text for m in sample.messages[3::2]]
    assert feedback == ["correct" if g == CORRECT else "incorrect" for g in grades]
    score = next(iter(sample.scores.values()))
    first = len(grades) if grades[-1] == CORRECT else None
    assert score.metadata["first_correct_attempt"] == first
    assert score.value == {
        f"success_at_{k}": int(first is not None and first <= k) for k in range(1, max(10, len(grades)) + 1)
    }


def test_unscored_grader_fails_without_false_feedback(tmp_path: Path) -> None:
    @scorer(metrics=[accuracy()])
    def broken_judge():
        async def score(state, target):
            return Score.unscored(explanation="Broken grader")

        return score

    model = get_model(
        "mockllm/model",
        custom_outputs=[ModelOutput.from_content("mockllm/model", "guess")],
    )
    task = Task(
        dataset=[Sample(input="Question?", target="HIDDEN-GOLD")],
        solver=binary_feedback_solver(broken_judge(), 10),
        scorer=feedback_curve(10),
    )
    log = eval(task, model=model, display="none", log_dir=str(tmp_path))[0]
    assert log.samples[0].error is not None
    assert "valid grade" in log.samples[0].error.message
    assert all(m.text != "incorrect" for m in log.samples[0].messages)


@pytest.mark.parametrize("structured", [True, False])
def test_simpleqa_structured_feedback_stops_on_correct(tmp_path, structured):
    @scorer(metrics=[accuracy()])
    def judge():
        async def score(state, target):
            correct = state.output.completion == "right"
            value = {"correct": float(correct), "incorrect": float(not correct), "not_attempted": 0.0} if structured else (CORRECT if correct else INCORRECT)
            return Score(value=value)
        return score
    model = get_model("mockllm/model", custom_outputs=[ModelOutput.from_content("mockllm/model", answer) for answer in ["wrong", "right", "must not generate"]])
    task = Task(dataset=[Sample(input="Question?", target="right")], solver=binary_feedback_solver(judge(), 100), scorer=feedback_curve(100))
    log = eval(task, model=model, display="none", log_dir=str(tmp_path))[0]
    assert log.status == "success", log.error
    sample = log.samples[0]
    assert sample.error is None
    assert [r["feedback"] for r in sample.metadata["feedback_attempts"]] == ["incorrect", "correct"]
    assert len(sample.metadata["feedback_attempts"]) == 2
    assert next(iter(sample.scores.values())).value["success_at_100"] == 1


def test_real_simpleqa_scorer_feedback_integration(tmp_path, monkeypatch):
    import importlib
    module = importlib.import_module('inspect_evals.simpleqa.scorer')
    grader = get_model('mockllm/grader', custom_outputs=[ModelOutput.from_content('mockllm/grader', x) for x in ['B', 'A']])
    monkeypatch.setattr(module, 'get_model', lambda **kwargs: grader)
    model = get_model('mockllm/model', custom_outputs=[ModelOutput.from_content('mockllm/model', x) for x in ['wrong', 'right', 'must not generate']])
    task = Task(dataset=[Sample(input='Question?', target='right')], solver=binary_feedback_solver(module.simpleqa_scorer(), 100), scorer=feedback_curve(100))
    log = eval(task, model=model, display='none', log_dir=str(tmp_path))[0]
    assert log.status == 'success', log.error
    sample = log.samples[0]
    assert sample.error is None
    assert [r['feedback'] for r in sample.metadata['feedback_attempts']] == ['incorrect', 'correct']
