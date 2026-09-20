import json
from types import SimpleNamespace

import pytest
from inspect_ai import Task, eval
from inspect_ai.dataset import Sample
from inspect_ai.model import ChatMessageAssistant, ModelOutput
from inspect_ai.solver import solver
from inspect_ai.util import ExecResult, store

from inspect_audit._scicode_audit import scicode_regrade


@pytest.mark.parametrize("variant", ["ordinary", "feedback", "scaling", "probe"])
def test_replay_executes_final_chain_instead_of_saved_grades(tmp_path, monkeypatch, variant):
    grader = pytest.importorskip("inspect_evals.scicode.scorer")
    executions = []

    async def execute(cmd, **kwargs):
        executions.append((cmd[-1], kwargs))
        return ExecResult(False, 1, "", "intentional test failure")

    monkeypatch.setattr(grader, "sandbox", lambda name=None: SimpleNamespace(exec=execute))
    solutions = {"1.1": "def first(): return 17", "1.2": "def second(): return first()"}
    steps = [dict(step_number=qid, test_cases=["assert second() == 19"])
             for qid in solutions]
    metadata = dict(sub_steps=steps, required_dependencies="import math",
                    _audit_source_task_args={"timeout": 23})

    @solver
    def history():
        async def solve(state, generate):
            state.output = ModelOutput.from_content("mockllm/model", "final")
            if variant == "feedback":
                state.metadata["feedback_solutions"] = solutions
                state.metadata["feedback_final_scores"] = {"1.1": 1, "1.2": 1}
            elif variant == "scaling":
                store().set("scicode_snapshots", [
                    {"solutions": {"1.1": "STALE_SNAPSHOT"}}, {"solutions": solutions}])
            elif variant == "probe":
                state.metadata["feedback_solutions"] = {"1.1": "STALE_FEEDBACK"}
                state.output = ModelOutput.from_content("mockllm/model", json.dumps({"solutions": solutions}))
            else:
                state.messages.extend(ChatMessageAssistant(content=f"```python\n{code}\n```")
                                      for code in solutions.values())
            return state
        return solve

    log = eval(Task(dataset=[Sample(input="question", metadata=metadata)],
                    solver=history(), scorer=scicode_regrade()),
               model="mockllm/model", display="none", log_dir=str(tmp_path))[0]
    assert log.status == "success", log.error
    assert len(executions) == 2
    assert "def first(): return 17" in executions[-1][0]
    assert "def second(): return first()" in executions[-1][0]
    assert "STALE" not in executions[-1][0]
    score = next(iter(log.samples[0].scores.values()))
    assert score.value == {"1.1": 0.0, "1.2": 0.0}
    assert score.metadata == {"reexecuted_tests": True, "timeout": 23}
