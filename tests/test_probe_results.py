"""Probe success depends on observed operations, not an unresolved validity label."""

import asyncio
import json

import pytest
from ia_compatibility.probe import ProbeAPI
from ia_compatibility.probe_results import outcomes
from inspect_ai.model import ChatMessageTool, GenerateConfig
from inspect_ai.tool import ToolCallError


def result(name: str, text: str = "ok", error: str | None = None) -> ChatMessageTool:
    return ChatMessageTool(
        content=text,
        function=name,
        error=ToolCallError(type="unknown", message=error) if error else None,
    )


def test_an_unresolved_verdict_does_not_hide_a_failed_grader() -> None:
    messages = [
        result("auditor_bash"),
        result("grade", error="sandbox unavailable"),
        result("record_verdict"),
        result("submit"),
    ]
    report = outcomes(messages, {"logs": False, "benchmark_box": False, "grader": True})
    assert report["status"] == "failed"
    assert report["stages"]["grading"] == "failed"


def test_a_disabled_judge_is_partial_not_a_pass() -> None:
    messages = [
        result("auditor_bash"),
        result("grade", error="API_FREE_PROBE: model-based grading is disabled"),
        result("record_verdict"),
        result("submit"),
    ]
    report = outcomes(messages, {"logs": False, "benchmark_box": False, "grader": True})
    assert report["status"] == "partial"
    assert report["stages"]["grading"] == "model_required"


def test_a_returned_incorrect_score_proves_grader_execution() -> None:
    messages = [
        result("auditor_bash"),
        result("grade", '{"scores":[{"value":"I"}]}'),
        result("record_verdict"),
        result("submit"),
    ]
    assert (
        outcomes(messages, {"logs": False, "benchmark_box": False, "grader": True})["status"]
        == "passed"
    )


def test_missing_operations_and_empty_grades_cannot_pass() -> None:
    assert outcomes([], {"logs": True, "benchmark_box": True, "grader": True})["status"] == "failed"
    assert (
        outcomes(
            [result("grade", '{"scores":[]}')],
            {"logs": False, "benchmark_box": False, "grader": True},
        )["stages"]["grading"]
        == "failed"
    )


def test_the_probe_cannot_generate_a_judge_response() -> None:
    with pytest.raises(RuntimeError, match="model-based grading is disabled"):
        asyncio.run(ProbeAPI("judge").generate([], [], "auto", GenerateConfig()))


def test_mocked_grading_is_visible_in_the_result() -> None:
    messages = [
        result("auditor_bash"),
        result("grade", '{"scores":[{"value":"I"}]}'),
        result("record_verdict"),
        result("submit"),
    ]
    report = outcomes(
        messages, {"logs": False, "benchmark_box": False, "grader": True, "mock_judge": True}
    )
    assert report["status"] == "passed"
    assert report["stages"]["grading"] == "passed_mock"


@pytest.mark.parametrize(
    "score",
    [
        {"value": float("nan")},
        {"value": {"score": float("nan"), "confidence": 100}},
        {"value": "I", "reason": "grader_failed"},
    ],
)
def test_unscored_judgments_are_not_successful_grader_execution(score: dict) -> None:
    report = outcomes(
        [result("grade", json.dumps({"scores": [score]}))],
        {"logs": False, "benchmark_box": False, "grader": True},
    )
    assert report["stages"]["grading"] == "failed"
