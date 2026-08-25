"""Mirroring the evaluated agent's tools for the auditor to enact.

The recovered benchmark tools are remounted under `benchmark_*`, keeping their
schema; enacting one records the call and its result into the session. These
exercise the naming, the schema preservation, the submit special-case, and the
recording; execution against a real benchmark box is covered under Docker.
"""


import anyio
from inspect_ai.scorer import match
from inspect_ai.tool import ToolDef, bash, python
from inspect_ai.tool._tool_def import tool_defs
from inspect_ai.util import Store

from inspect_audit._agent import audit_items, auditor_tools
from inspect_audit._contract import SolverContract
from inspect_audit._state import (
    BenchmarkState,
    _as_text,
    benchmark_tools,
    record_enacted,
)


def test_mirrored_tools_keep_the_agents_names_and_schema() -> None:
    contract = SolverContract(tools=[ToolDef(bash()), ToolDef(python())])
    mirrored = benchmark_tools(contract.tools, "/audit")

    defs = {d.name: d for d in anyio.run(tool_defs, mirrored)}
    assert set(defs) == {"benchmark_bash", "benchmark_python"}
    # the agent's own schema, unchanged
    assert "command" in defs["benchmark_bash"].parameters.properties
    assert "code" in defs["benchmark_python"].parameters.properties


def test_a_submit_shaped_tool_becomes_a_terminal_not_a_command() -> None:
    async def submit(answer: str) -> str:
        """Submit the answer.

        Args:
            answer: The answer.
        """
        return answer

    mirrored = benchmark_tools([ToolDef(submit, name="submit")], "/audit")
    (d,) = anyio.run(tool_defs, mirrored)

    assert d.name == "benchmark_submit"
    assert set(d.parameters.properties) == {"answer"}
    assert "ends the attempt" in d.description


def test_record_enacted_splits_authored_call_from_enacted_result() -> None:
    state = BenchmarkState(store=Store())
    record_enacted(state, "bash", {"command": "ls"}, "file.txt")

    # the choice to call is the auditor's (authored); the output is the box's (enacted)
    assert [m.provenance for m in state.messages] == ["authored", "enacted"]
    call, result = state.messages[0].message, state.messages[1].message
    assert call.tool_calls is not None and call.tool_calls[0].function == "bash"
    assert result.tool_call_id == call.tool_calls[0].id
    assert result.text == "file.txt"


def test_as_text_handles_string_and_content_results() -> None:
    from inspect_ai._util.content import ContentImage, ContentText

    assert _as_text("plain") == "plain"
    assert _as_text([ContentText(text="a"), ContentText(text="b")]) == "a\nb"
    # a non-text result still yields something recordable rather than raising
    assert _as_text([ContentImage(image="data:...")])


def test_auditor_mounts_the_family_only_with_a_box() -> None:
    contract = SolverContract(tools=[ToolDef(bash())])

    mounted_with = _auditor_tool_names(contract, benchmark=True)
    mounted_without = _auditor_tool_names(contract, benchmark=False)

    assert "benchmark_bash" in mounted_with
    assert "benchmark_bash" not in mounted_without
    # our own instruments are present either way, under the audit_* namespace
    assert {"audit_bash", "audit_probe"} <= mounted_with
    assert {"audit_bash", "audit_probe"} <= mounted_without
    # the old namespace is gone: benchmark_bash now means the mirrored tool only
    assert "bash" not in mounted_with


def _auditor_tool_names(contract: SolverContract, *, benchmark: bool) -> set[str]:
    (item,) = audit_items(["gold-answer"])
    tools = auditor_tools(
        [item], contract=contract, benchmark=benchmark, benchmark_scorers=match()
    )
    return {d.name for d in anyio.run(tool_defs, tools)}
