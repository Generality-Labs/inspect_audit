"""Sequential SciCode submissions with binary test feedback."""

from copy import deepcopy

from inspect_ai import Task, task, task_with
from inspect_ai.model import ChatMessageAssistant, ChatMessageUser, get_model
from inspect_ai.scorer import Score, scorer
from inspect_ai.solver import solver, system_message


@solver
def scicode_feedback_solver(max_attempts: int, timeout: int):
    from inspect_evals.scicode.prompt_templates import SUBPROBLEM_PROMPT
    from inspect_evals.scicode.scorer import verify_subproblem
    from inspect_evals.scicode.util import extract_code

    async def solve(state, generate):
        solutions, final_scores, records = {}, {}, []
        state.metadata["feedback_attempts"] = records
        state.metadata["feedback_scope"] = "subproblem"
        for step in state.metadata["sub_steps"]:
            step_id = step["step_number"]
            state.messages.append(
                ChatMessageUser(content=SUBPROBLEM_PROMPT.format(**step))
            )
            if step.get("provided_code") is not None:
                state.messages.append(
                    ChatMessageAssistant(
                        content=f"```python\n{step['provided_code']}\n```",
                        source="operator",
                    )
                )
                continue
            for attempt in range(1, max_attempts + 1):
                output = await get_model().generate(state.messages)
                if output.stop_reason != "stop" or not output.completion.strip():
                    raise RuntimeError(
                        f"Incomplete answer for {step_id}; no incorrect feedback issued"
                    )
                state.output = output
                state.messages.append(output.message)
                solutions[step_id] = extract_code(output.completion)
                judge = verify_subproblem(step, timeout, submitted_code=solutions)
                result = await judge(state, state.target)
                value = (
                    result.value.get(step_id)
                    if result and isinstance(result.value, dict)
                    else None
                )
                if value not in (0, 1):
                    raise RuntimeError(f"Invalid SciCode grade for {step_id}")
                final_scores[step_id] = value
                feedback = "correct" if value == 1 else "incorrect"
                records.append(
                    {
                        "subproblem": step_id,
                        "attempt": attempt,
                        "feedback": feedback,
                        "grade": result.model_dump(mode="json"),
                    }
                )
                state.messages.append(ChatMessageUser(content=feedback))
                if value == 1:
                    break
        state.metadata["feedback_final_scores"] = final_scores
        state.metadata["feedback_solutions"] = solutions
        state.completed = True
        return state

    return solve


def feedback_scorer():
    from inspect_evals.scicode.metrics import (
        percentage_main_problems_solved,
        percentage_subproblems_solved,
        total_main_problems_solved,
        total_subproblems_solved,
    )

    @scorer(
        metrics=[
            percentage_main_problems_solved(),
            percentage_subproblems_solved(),
            total_main_problems_solved(),
            total_subproblems_solved(),
        ]
    )
    def recorded_scicode():
        async def score(state, target):
            values = state.metadata["feedback_final_scores"]
            if not values:
                raise RuntimeError("No SciCode grades recorded")
            return Score(
                value=values, metadata={"attempts": state.metadata["feedback_attempts"]}
            )

        return score

    return recorded_scicode()


@task
def scicode_feedback(max_attempts: int = 10, timeout: int = 300) -> Task:
    from inspect_evals.scicode.prompt_templates import INITIAL_PROMPT
    from inspect_evals.scicode.scicode import scicode

    if not 1 <= max_attempts <= 100:
        raise ValueError("max_attempts must be between 1 and 100")
    base = scicode(
        scaling=False,
        provide_scientific_background=False,
        include_dev_set=False,
        timeout=timeout,
    )
    instructions = (
        f"You have up to {max_attempts} submissions per subproblem. After each submission you receive only correct or incorrect. "
        "If incorrect, revise that subproblem. If correct or attempts are exhausted, continue to the next subproblem. "
        "Earlier subproblem implementations remain fixed. Official tests and error details are not provided."
    )
    return task_with(
        base,
        solver=[
            system_message(INITIAL_PROMPT),
            system_message(instructions),
            scicode_feedback_solver(max_attempts, timeout),
        ],
        scorer=feedback_scorer(),
        metadata={
            **deepcopy(base.metadata or {}),
            "feedback_scope": "subproblem",
            "max_attempts": max_attempts,
            "stop_on_correct": True,
        },
    )
