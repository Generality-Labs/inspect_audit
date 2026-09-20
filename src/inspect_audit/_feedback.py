"""Repeated answers with binary feedback, without exposing the answer key."""

import hashlib
import json
import os
import tempfile
import urllib.request
from copy import deepcopy
from typing import Any

from inspect_ai import Task, task
from inspect_ai.log import EvalSample, read_eval_log
from inspect_ai.model import (
    ChatMessageSystem,
    ChatMessageUser,
    ModelInfo,
    get_model,
    set_model_info,
)
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
def binary_feedback_solver(judge: Scorer, max_attempts: int, resumes: dict[str, EvalSample] | None = None) -> Solver:
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
        pending = None
        previous = (resumes or {}).get(str(state.sample_id))
        if previous is not None:
            records = deepcopy((previous.metadata or {})["feedback_attempts"])
            state.messages = deepcopy(previous.messages)
            # A complete but ungraded answer is retried with the judge, not
            # regenerated. Incomplete/filtered answers are not submissions.
            if len(state.messages) == 2 + 2 * len(records) + 1:
                if previous.output.choices and previous.output.stop_reason == "stop" and previous.output.completion.strip():
                    pending = previous.output.model_copy(deep=True)
                else:
                    state.messages.pop()
            elif len(state.messages) != 2 + 2 * len(records):
                raise ValueError("Unexpected resume conversation shape")
            state.metadata["resumed_attempts"] = len(records)
        state.metadata["feedback_attempts"] = records
        state.metadata["feedback_max_attempts"] = max_attempts
        for number in range(len(records) + 1, max_attempts + 1):
            if pending is not None:
                output, pending = pending, None
            else:
                output = await get_model().generate(state.messages)
                state.messages.append(output.message)
            state.output = output
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
            grade = result.value if result is not None else None
            if isinstance(grade, dict) and set(grade) == {"correct", "incorrect", "not_attempted"}:
                if all(v in (0, 1) for v in grade.values()) and sum(grade.values()) == 1:
                    grade = next(code for key, code in (("correct", CORRECT), ("incorrect", INCORRECT), ("not_attempted", NOANSWER)) if grade[key] == 1)
            if (
                result is None
                or not isinstance(grade, str)
                or grade not in (CORRECT, INCORRECT, NOANSWER)
            ):
                detail = result.model_dump_json() if result is not None else "None"
                raise RuntimeError(f"Grader did not return a valid grade: {detail}")
            feedback = "correct" if grade == CORRECT else "incorrect"
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
def binary_feedback(
    task: str,
    max_attempts: int = 10,
    model_info: dict[str, dict[str, Any]] | None = None,
    task_args: dict[str, Any] | None = None,
    resume_log: str | None = None,
    resume_sha256: str | None = None,
) -> Task:
    """Run a text benchmark with up to 100 binary-feedback answer attempts.

    Args:
        task: Inspect benchmark task with one categorical C/I/N scorer.
        max_attempts: Maximum answers per question; stop at the first correct answer.
        task_args: Arguments passed to the underlying benchmark.
        model_info: Explicit Inspect metadata for models absent from its local database.
        resume_log: Local log or env: variable containing a secret presigned URL.
        resume_sha256: Required SHA-256 for a remotely downloaded continuation log.
    """
    if not 1 <= max_attempts <= 100:
        raise ValueError("max_attempts must be between 1 and 100")
    for name, info in (model_info or {}).items():
        set_model_info(name, ModelInfo.model_validate(info))
    benchmark = resolve_task(task, task_args) if task_args else resolve_task(task)
    scorers = benchmark.scorer or []
    if len(scorers) != 1:
        raise ValueError("binary_feedback requires exactly one benchmark scorer")
    if any(not isinstance(sample.input, str) for sample in benchmark.dataset):
        raise ValueError("binary_feedback currently supports text questions only")
    # Hawk filters sample IDs before Inspect's evaluation-time ID assignment.
    dataset = deepcopy(benchmark.dataset)
    for position, sample in enumerate(dataset, 1):
        if sample.id is None:
            sample.id = position
    resumes: dict[str, EvalSample] = {}
    if resume_log:
        if resume_log.startswith("env:"):
            # The submitting user's presigned URL is passed as a runner secret;
            # it must never appear in config, metadata or model conversations.
            url = os.environ[resume_log.removeprefix("env:")]
            with tempfile.NamedTemporaryFile(suffix=".eval") as local:
                digest = hashlib.sha256()
                with urllib.request.urlopen(url, timeout=120) as response:
                    while chunk := response.read(1024 * 1024):
                        digest.update(chunk)
                        local.write(chunk)
                local.flush()
                if not resume_sha256 or digest.hexdigest() != resume_sha256:
                    raise ValueError("Resume log checksum mismatch")
                previous = read_eval_log(local.name, resolve_attachments=True)
        else:
            previous = read_eval_log(resume_log, resolve_attachments=True)
        for prior_sample in previous.samples or []:
            records = (prior_sample.metadata or {}).get("feedback_attempts", [])
            if prior_sample.error and not any(r["feedback"] == "correct" for r in records) and len(records) < max_attempts:
                if (prior_sample.metadata or {}).get("feedback_max_attempts") != max_attempts:
                    raise ValueError("Resume requires the same attempt budget")
                resumes[str(prior_sample.id)] = prior_sample
        dataset = dataset.filter(lambda s: str(s.id) in resumes)
        if len(dataset) != len(resumes) or not resumes:
            raise ValueError("Resume samples must match the benchmark dataset")
        for sample in dataset:
            old = resumes[str(sample.id)]
            if old.input != sample.input or old.target != sample.target:
                raise ValueError(f"Resume benchmark mismatch for sample {sample.id}")
    return Task(
        dataset=dataset,
        solver=binary_feedback_solver(scorers[0], max_attempts, resumes),
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
            "resume_log": resume_log,
            "resume_sha256": resume_sha256,
        },
    )
