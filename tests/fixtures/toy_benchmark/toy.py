"""A toy sandboxed benchmark whose grade depends on both the transcript and the box.

Each sample delivers a file and a setup script into its box; a `setup=` solver
marks the store; the scorer reads the box (setup must have run) and compares
the completion with the target. `SCORER_CALLS` counts every grade so a test can
tell who called the grader and how often.
"""

from pathlib import Path

from inspect_ai import Task, task
from inspect_ai.dataset import Sample
from inspect_ai.scorer import CORRECT, INCORRECT, Score, Target, accuracy, scorer
from inspect_ai.solver import Generate, TaskState, generate, solver
from inspect_ai.util import sandbox

COMPOSE = Path(__file__).parent / "compose.yaml"
SETUP = "echo ready > /work/setup_done"
SCORER_CALLS: list[str] = []


@solver
def mark_setup():
    async def solve(state: TaskState, generate: Generate) -> TaskState:
        state.store.set("their_setup_ran", True)
        return state

    return solve


@scorer(metrics=[accuracy()])
def box_scorer():
    async def score(state: TaskState, target: Target) -> Score:
        SCORER_CALLS.append(str(state.sample_id))
        ready = await sandbox().exec(["cat", "/work/setup_done"])
        if not ready.success:
            return Score(value=INCORRECT, explanation="box not set up")
        answer = state.output.completion.strip()
        return Score(value=CORRECT if answer == target.text else INCORRECT, answer=answer)

    return score


def samples() -> list[Sample]:
    return [
        Sample(
            id=sid,
            input=question,
            target=answer,
            files={"/work/input.txt": question},
            setup=SETUP,
        )
        for sid, question, answer in [("a", "What is 6*7?", "42"), ("b", "What is 3*3?", "9")]
    ]


@task
def toy() -> Task:
    return Task(
        dataset=samples(),
        setup=mark_setup(),
        solver=generate(),
        scorer=box_scorer(),
        sandbox=("docker", str(COMPOSE)),
        message_limit=3,
    )
