"""Operational outcomes from actual probe tool results, separate from validity."""

import json
import math
from collections.abc import Sequence
from typing import Any

from inspect_ai.model import ChatMessage
from inspect_ai.scorer import Score


def outcomes(messages: Sequence[ChatMessage], expected: dict[str, bool]) -> dict[str, Any]:
    results = {m.function: m for m in messages if m.role == "tool"}
    stages: dict[str, str] = {}
    errors: dict[str, str] = {}
    for stage, tool, applicable in [
        ("sample_and_source", "investigator_bash", True),
        ("recorded_attempts", "investigator_bash", expected["logs"]),
        ("benchmark_shell", "benchmark_bash", expected["benchmark_box"]),
        ("grading", "grade", expected["grader"]),
        ("verdict", "record_verdict", True),
        ("submission", "submit", True),
    ]:
        if not applicable:
            stages[stage] = "not_applicable"
            continue
        result = results.get(tool)
        if result is None:
            stages[stage] = "not_reached"
        elif result.error:
            stages[stage] = (
                "model_required" if "API_FREE_PROBE" in result.error.message else "failed"
            )
            errors[stage] = result.error.message
        else:
            stages[stage] = "passed"
            if stage == "recorded_attempts":
                try:
                    if not json.loads(result.text)["attempts"]:
                        raise ValueError("no recorded attempts staged in /audit/logs")
                except (ValueError, KeyError, TypeError) as ex:
                    stages[stage] = "failed"
                    errors[stage] = str(ex)
            if tool == "grade":
                try:
                    scores = json.loads(result.text)["scores"]
                    if not scores:
                        raise ValueError("Grader returned no scores")

                    def scored(value: Any) -> bool:
                        if isinstance(value, dict):
                            return any(scored(v) for v in value.values())
                        if isinstance(value, list):
                            return any(scored(v) for v in value)
                        return value is not None and not (
                            isinstance(value, float) and not math.isfinite(value)
                        )

                    if any(
                        s.reason in {"grader_failed", "scoring_failed"} or not scored(s.value)
                        for s in (Score.model_validate(value) for value in scores)
                    ):
                        raise ValueError("Grader returned an unscored or failed judgment")
                    if expected.get("mock_judge"):
                        stages[stage] = "passed_mock"
                except (ValueError, KeyError, TypeError) as ex:
                    stages[stage] = "failed"
                    errors[stage] = str(ex)
            elif tool == "attempts" and not result.text.lstrip().startswith("["):
                stages[stage] = "failed"
                errors[stage] = result.text
    status = (
        "failed"
        if any(v in ("failed", "not_reached") for v in stages.values())
        else "partial"
        if "model_required" in stages.values()
        else "passed"
    )
    return {"status": status, "stages": stages, "errors": errors}
