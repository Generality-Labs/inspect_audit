"""Configured mock responses for grader integration tests, never accuracy labels."""

from typing import Any

from inspect_ai.model import ChatMessage, GenerateConfig, Model, ModelOutput, get_model
from inspect_ai.tool import ToolChoice, ToolInfo

MOCK_JUDGE = "mockllm/compatibility-judge"


def mock_judge(response: dict[str, Any]) -> Model:
    if not isinstance(response, dict):
        raise ValueError("A mock judge response must be a mapping")
    if (
        set(response) == {"tool", "arguments"}
        and isinstance(response["tool"], str)
        and isinstance(response["arguments"], dict)
    ):
        output = ModelOutput.for_tool_call(MOCK_JUDGE, response["tool"], response["arguments"])
    elif set(response) == {"content"} and isinstance(response["content"], str):
        output = ModelOutput.from_content(MOCK_JUDGE, response["content"])
    else:
        raise ValueError(
            "A mock judge needs either content: text, or tool: name with arguments: mapping"
        )

    def respond(
        messages: list[ChatMessage],
        tools: list[ToolInfo],
        choice: ToolChoice,
        config: GenerateConfig,
    ) -> ModelOutput:
        if "tool" in response and response["tool"] not in {t.name for t in tools}:
            raise ValueError(
                f"Mock response names a tool the grader did not expose: {response['tool']}"
            )
        return output.model_copy(deep=True)

    return get_model(MOCK_JUDGE, custom_outputs=respond)
