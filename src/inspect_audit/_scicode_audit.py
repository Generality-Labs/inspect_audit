"""Replay actual SciCode tests across ordinary, scaling and feedback histories."""

import json
from typing import Any

from inspect_ai import Task, task, task_with
from inspect_ai.scorer import Score, Scorer, Target, scorer
from inspect_ai.solver import TaskState
from inspect_ai.util import store


def submitted_solutions(state: TaskState) -> dict[str, str]:
    """Recover the recorded chain, or accept an explicit JSON chain for a new probe."""
    try:
        candidate = json.loads(state.output.completion)
    except ValueError:
        candidate = None
    if isinstance(candidate, dict) and "solutions" in candidate:
        solutions = candidate["solutions"]
        if not isinstance(solutions, dict) or any(not isinstance(k, str) or not isinstance(v, str) for k, v in solutions.items()):
            raise ValueError("solutions must map subproblem IDs to Python code strings")
        return solutions
    if "feedback_solutions" in state.metadata:
        return dict(state.metadata["feedback_solutions"])
    snapshots: list[dict[str, Any]] = store().get("scicode_snapshots", [])
    if snapshots:
        return dict(snapshots[-1]["solutions"])
    from inspect_evals.scicode.util import get_solution_code

    return {step["step_number"]: code for step, code in
            zip(state.metadata["sub_steps"], get_solution_code(state), strict=True)}


@scorer(metrics=[])
def scicode_regrade(timeout: int = 300) -> Scorer:
    """Execute the original tests, never trust stored feedback_final_scores."""
    from inspect_evals.scicode.scorer import verify_subproblem

    async def score(state: TaskState, target: Target) -> Score:
        solutions = submitted_solutions(state)
        seconds = int(state.metadata.get("_audit_source_task_args", {}).get("timeout", timeout))
        values: dict[str, Any] = {}
        explanations: list[str] = []
        for step in state.metadata["sub_steps"]:
            check = verify_subproblem(step, seconds, submitted_code=solutions)
            if check is None:
                continue
            result = await check(state, target)
            if result is None or not isinstance(result.value, dict):
                raise RuntimeError(f"No grade for {step['step_number']}")
            values.update(result.value)
            explanations.append(result.explanation or "")
        return Score(value=values, explanation="\n\n".join(explanations),
                     metadata={"reexecuted_tests": True, "timeout": seconds})

    return score


@task
def scicode_replay(timeout: int = 300) -> Task:
    """SciCode's original dataset and box with a scorer for all recorded variants."""
    from inspect_evals.scicode.scicode import scicode

    original = scicode(timeout=timeout, scaling=False)
    return task_with(original, scorer=scicode_regrade(timeout),
                     metadata={**(original.metadata or {}), "audit_source_modules": [
                         "inspect_evals.scicode.scicode", "inspect_evals.scicode.scorer",
                         "inspect_evals.scicode.util", "inspect_evals.scicode.test_util",
                     ]})
