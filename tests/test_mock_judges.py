"""Real benchmark graders parse the configured mock replies through the grade tool."""

import json
from pathlib import Path

import pytest
import yaml
from compatibility.run_suite import summarize
from ia_compatibility.judges import MOCK_JUDGE, mock_judge
from ia_compatibility.probe_results import outcomes
from inspect_ai import Task, eval
from inspect_ai.dataset import Sample
from inspect_ai.event import ModelEvent
from inspect_ai.log import read_eval_log
from inspect_ai.model import ChatMessageTool
from inspect_ai.solver import Generate, TaskState, solver
from inspect_evals.hle.judge import llm_grader
from inspect_evals.simpleqa.scorer import simpleqa_schema_tool_scorer

from inspect_audit._grade import grade_tool

CASES = Path(__file__).parent / "compatibility" / "cases"


@pytest.mark.parametrize("case", ["simpleqa", "simpleqa-verified", "hle"])
def test_benchmark_grader_parses_mock_reply(case: str, tmp_path: Path) -> None:
    spec = yaml.safe_load((CASES / f"{case}.eval-set.yaml").read_text())["tasks"][0]["items"][0][
        "args"
    ]["spec"]
    grader = llm_grader(max_grader_attempts=1) if case == "hle" else simpleqa_schema_tool_scorer()

    @solver
    def exercise():
        async def solve(state: TaskState, generate: Generate) -> TaskState:
            result = json.loads(
                await grade_tool(state, list(state.messages), [grader], None)("candidate", "", 0)
            )
            [score] = result["scores"]
            assert score["value"] == ({"score": "I", "confidence": 100} if case == "hle" else "I")
            assert score["reason"] != "grader_failed"
            assert "Scripted mock judgment" in score["explanation"]
            state.store.set("grade_result", result)
            return state

        return solve

    [log] = eval(
        Task(
            dataset=[Sample(input="A question", target="gold")],
            solver=exercise(),
            model_roles={"grader": mock_judge(spec["mock_judges"]["grader"])},
            metadata={"functionality_probe": {"mock_judge": True}},
        ),
        model="mockllm/model",
        log_dir=str(tmp_path),
        display="none",
    )
    assert log.status == "success", log.error
    sample = (read_eval_log(log.location).samples or [])[0]
    assert any(isinstance(e, ModelEvent) and e.model == MOCK_JUDGE for e in sample.events)
    report = summarize([Path(log.location)])
    assert report["samples"][0]["external_models"] == []
    assert report["samples"][0]["mock_models"] == [MOCK_JUDGE]


@pytest.mark.parametrize(
    "response",
    [{}, {"content": 7}, {"tool": "submit"}, {"content": "x", "tool": "submit", "arguments": {}}],
)
def test_invalid_mock_config_fails_before_running(response: dict) -> None:
    with pytest.raises(ValueError, match="mock judge needs"):
        mock_judge(response)


def test_malformed_hle_judgment_preserves_failure_reason(tmp_path: Path) -> None:
    @solver
    def exercise():
        async def solve(state: TaskState, generate: Generate) -> TaskState:
            raw = await grade_tool(
                state, list(state.messages), [llm_grader(max_grader_attempts=1)], None
            )("candidate", "", 0)
            score = json.loads(raw)["scores"][0]
            assert score["reason"] == "grader_failed"
            assert score["metadata"]["grader_failure_mode"] == "grader_schema_mismatch"
            report = outcomes(
                [ChatMessageTool(function="grade", content=raw)],
                {"logs": False, "benchmark_box": False, "grader": True},
            )
            assert report["stages"]["grading"] == "failed"
            return state

        return solve

    [log] = eval(
        Task(
            dataset=[Sample(input="Question", target="Gold")],
            solver=exercise(),
            model_roles={"grader": mock_judge({"content": "Not JSON"})},
        ),
        model="mockllm/model",
        log_dir=str(tmp_path),
        display="none",
    )
    assert log.status == "success", log.error
