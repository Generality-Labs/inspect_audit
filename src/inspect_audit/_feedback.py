"""Repeated answers with binary feedback, without exposing the answer key."""

import hashlib
import json
from copy import deepcopy
from typing import Any

from inspect_ai import Task, task
from inspect_ai.model import ChatMessageSystem, ChatMessageUser, get_model
from inspect_ai.scorer import (
    CORRECT,
    INCORRECT,
    NOANSWER,
    Score,
    Scorer,
    Target,
    mean,
    scorer,
)
from inspect_ai.solver import Generate, Solver, TaskState, solver

from ._resolve import resolve_task

ANTI_ABSTENTION = (
    "This is a guessing game, not a test. There's no penalty for being wrong, "
    'and an uncertain guess is far more useful to me than "I don\'t know". '
    "Give your single best guess."
)


@solver
def binary_feedback_solver(judge: Scorer, max_attempts: int) -> Solver:
    """Retain answer history and return only binary feedback to the answering model."""

    async def solve(state: TaskState, generate: Generate) -> TaskState:
        question = state.input_text
        benchmark_metadata = deepcopy(state.metadata)
        state.messages = [
            ChatMessageSystem(
                content=(
                    f"You have up to {max_attempts} attempts to answer the question. "
                    "Submit one concise answer per turn. After each answer you will receive "
                    "only 'correct' or 'incorrect'. If incorrect, reconsider and submit "
                    "your next single best answer. The interaction ends when your answer "
                    "is correct or you have used all attempts."
                )
            ),
            ChatMessageUser(content=f"{question}\n\n{ANTI_ABSTENTION}"),
        ]
        records: list[dict[str, Any]] = []
        state.metadata["feedback_attempts"] = records
        state.metadata["feedback_max_attempts"] = max_attempts
        for number in range(1, max_attempts + 1):
            output = await get_model().generate(state.messages)
            state.output = output
            state.messages.append(output.message)
            if output.stop_reason != "stop" or not output.completion.strip():
                raise RuntimeError(
                    f"Attempt {number} did not produce a complete answer "
                    f"(stop_reason={output.stop_reason!r}); not treating this as incorrect."
                )
            # The judge gets the unchanged question and only this answer. Neither
            # the suffix nor the retry conversation changes the grading problem.
            graded_state = TaskState(
                model=state.model,
                sample_id=state.sample_id,
                epoch=state.epoch,
                input=question,
                target=state.target,
                messages=[ChatMessageUser(content=question), output.message],
                output=output,
                metadata=deepcopy(benchmark_metadata),
            )
            result = await judge(graded_state, state.target)
            if (
                result is None
                or not isinstance(result.value, str)
                or result.value not in (CORRECT, INCORRECT, NOANSWER)
            ):
                detail = result.model_dump_json() if result is not None else "None"
                raise RuntimeError(f"Grader did not return a valid grade: {detail}")
            feedback = "correct" if result.value == CORRECT else "incorrect"
            records.append(
                {
                    "attempt": number,
                    "answer": output.completion,
                    "feedback": feedback,
                    "grade": result.model_dump(mode="json"),
                }
            )
            state.messages.append(ChatMessageUser(content=feedback))
            if feedback == "correct":
                break
        state.completed = True
        return state

    return solve


@scorer(metrics={"*": [mean()]})
def feedback_curve(max_attempts: int) -> Scorer:
    """Score success by each attempt budget without calling the judge again."""

    async def score(state: TaskState, target: Target) -> Score:
        records = state.metadata["feedback_attempts"]
        first = next(
            (r["attempt"] for r in records if r["feedback"] == "correct"), None
        )
        return Score(
            value={
                f"success_at_{k}": int(first is not None and first <= k)
                for k in range(1, max_attempts + 1)
            },
            answer=state.output.completion,
            metadata={
                "first_correct_attempt": first,
                "attempts_used": len(records),
                "max_attempts": max_attempts,
                "attempts": records,
            },
        )

    return score


@task
def binary_feedback(task: str, max_attempts: int = 10) -> Task:
    """Run a text benchmark with up to ten binary-feedback answer attempts.

    Args:
        task: Inspect benchmark task with one categorical C/I/N scorer.
        max_attempts: Maximum answers per question; stop at the first correct answer.
    """
    if not 1 <= max_attempts <= 10:
        raise ValueError("max_attempts must be between 1 and 10")
    benchmark = resolve_task(task)
    scorers = benchmark.scorer or []
    if len(scorers) != 1:
        raise ValueError("binary_feedback requires exactly one benchmark scorer")
    if any(not isinstance(sample.input, str) for sample in benchmark.dataset):
        raise ValueError("binary_feedback currently supports text questions only")
    return Task(
        dataset=benchmark.dataset,
        solver=binary_feedback_solver(scorers[0], max_attempts),
        scorer=feedback_curve(max_attempts),
        version=1,
        metadata={
            "benchmark_task": task,
            "benchmark_version": benchmark.version,
            "benchmark_metadata": benchmark.metadata,
            "dataset_sha256": hashlib.sha256(
                json.dumps(
                    [
                        {
                            "id": sample.id,
                            "input": sample.input,
                            "target": sample.target,
                        }
                        for sample in benchmark.dataset
                    ],
                    ensure_ascii=False,
                    sort_keys=True,
                ).encode()
            ).hexdigest(),
            "max_attempts": max_attempts,
            "anti_abstention_suffix": ANTI_ABSTENTION,
            "feedback": "binary",
            "stop_on_correct": True,
        },
    )
