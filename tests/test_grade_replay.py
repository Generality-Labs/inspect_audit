"""Replay actual Inspect choice logs without treating reasoning as a bare letter."""

import json
from pathlib import Path

from inspect_ai import Task, eval
from inspect_ai.dataset import Sample
from inspect_ai.model import ChatMessageAssistant, ChatMessageUser, ModelOutput, get_model
from inspect_ai.scorer import choice
from inspect_ai.solver import Generate, TaskState, multiple_choice, solver
from inspect_ai.util import sample_limits

from inspect_audit._grade import grade_tool
from inspect_audit._logs import index_logs


def test_recorded_multiple_choice_reasoning_restores_the_selected_choice(tmp_path: Path) -> None:
    item = Sample(id="sample", input="Which is two?", choices=["one", "two"], target="B")
    # Deliberately wrong choice: restoration must preserve it, not substitute gold.
    completion = "Some recorded reasoning.\nANSWER: A"

    @solver
    def history():
        async def solve(state: TaskState, generate: Generate) -> TaskState:
            for _ in range(20):
                state.messages.extend(
                    [
                        ChatMessageUser(content="Earlier turn"),
                        ChatMessageAssistant(content="Earlier reply"),
                    ]
                )
            return state

        return solve

    [original] = eval(
        Task(
            name="choice_fixture",
            dataset=[item],
            solver=[history(), multiple_choice()],
            scorer=choice(),
        ),
        model=get_model(
            "mockllm/model", custom_outputs=[ModelOutput.from_content("mockllm/model", completion)]
        ),
        log_dir=str(tmp_path / "original"),
        display="none",
    )
    assert original.status == "success", original.error
    index = index_logs([original.location], task_name="choice_fixture", task_args={}, package=None)

    @solver
    def replay():
        async def solve(state: TaskState, generate: Generate) -> TaskState:
            assert sample_limits().message.limit == 10
            result = json.loads(
                await grade_tool(state, state.messages, [choice()], index)(
                    "", original.eval.eval_id, 1
                )
            )
            assert sample_limits().message.limit == 10
            assert result["graded"] == "recorded"
            [score] = result["scores"]
            assert score["value"] == "I" and score["answer"] == "A"
            assert "Some recorded reasoning" in score["explanation"]
            return state

        return solve

    [checked] = eval(
        Task(dataset=[item], solver=replay(), message_limit=10),
        model="mockllm/model",
        log_dir=str(tmp_path / "replay"),
        display="none",
    )
    assert checked.status == "success", checked.error
