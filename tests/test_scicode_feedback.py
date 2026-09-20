from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from inspect_ai import Task, eval
from inspect_ai.dataset import Sample
from inspect_ai.model import ModelOutput, get_model
from inspect_ai.util import ExecResult

from inspect_audit._scicode_feedback import feedback_scorer, scicode_feedback_solver


@pytest.mark.parametrize("first_pass", [True, False])
def test_real_scicode_grader_feedback(tmp_path, monkeypatch, first_pass):
    import inspect_evals.scicode.scorer as grader

    executions = []

    async def execute(cmd, **kwargs):
        executions.append(cmd[-1])
        passed = len(executions) > (1 if first_pass else 10)
        return ExecResult(passed, 0 if passed else 1, "", "HIDDEN_ERROR")

    monkeypatch.setattr(
        grader, "sandbox", lambda name=None: SimpleNamespace(exec=execute)
    )
    steps = []
    for n in range(1, 4):
        step = {
            "step_number": f"1.{n}",
            "step_description_prompt": f"Description {n}",
            "function_header": f"def f{n}():",
            "return_line": "return 1",
            "test_cases": ["assert HIDDEN_EXPECTED"],
        }
        if n == 2:
            step["provided_code"] = "def f2(): return 2"
        steps.append(step)
    metadata = {"sub_steps": steps, "required_dependencies": "import math"}
    outputs = [
        ModelOutput.from_content(
            "mockllm/model", "```python\ndef candidate(): return 1\n```"
        )
        for _ in range(12)
    ]
    model = get_model("mockllm/model", custom_outputs=outputs)
    t = Task(
        dataset=[Sample(input="1", metadata=metadata)],
        solver=scicode_feedback_solver(10, 300),
        scorer=feedback_scorer(),
    )
    log = eval(t, model=model, display="none", log_dir=str(tmp_path))[0]
    assert log.status == "success", log.error
    sample = log.samples[0]
    assert sample.error is None
    records = sample.metadata["feedback_attempts"]
    assert len(records) == (3 if first_pass else 11)
    assert records[-1]["subproblem"] == "1.3" and records[-1]["feedback"] == "correct"
    assert all(r["subproblem"] != "1.2" for r in records)
    assert all("HIDDEN" not in m.text for m in sample.messages)
    assert "def f2(): return 2" in executions[-1]
    values = next(iter(sample.scores.values())).value
    assert values == {"1.1": 1.0 if first_pass else 0.0, "1.3": 1.0}
    first_assistant = next(
        i for i, m in enumerate(sample.messages) if m.role == "assistant"
    )
    assert "Description 3" not in str(sample.messages[:first_assistant])
