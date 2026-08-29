"""Mirroring the evaluated agent's tools for the auditor to enact.

The recovered benchmark tools are remounted under `benchmark_*`, keeping their
schema; enacting one records the call and its result into the session. These
exercise the naming, the schema preservation, the submit special-case, and the
recording; execution against a real benchmark box is covered under Docker.
"""


from types import SimpleNamespace

import anyio
import pytest
from inspect_ai.scorer import match
from inspect_ai.tool import ToolDef, ToolError, bash, python
from inspect_ai.tool._tool_def import tool_defs
from inspect_ai.util import Store

import inspect_audit._agent as agent_module
from inspect_audit._agent import audit_items, auditor_tools, reset_benchmark
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


def _patch_reset(monkeypatch, *, soft_raises: bool = False, phoenix_raises: bool = False):
    """Drive reset_benchmark with fake restore/phoenix and a live session store."""
    session = BenchmarkState(store=Store())
    monkeypatch.setattr(
        agent_module,
        "sample_state",
        lambda: SimpleNamespace(
            metadata={
                "benchmark_setup": "echo hi",
                "benchmark_files": {"benchmark:/work/f.txt": "/host/f.txt"},
            }
        ),
    )
    monkeypatch.setattr(agent_module, "store_as", lambda _cls: session)
    seen: dict = {}

    async def fake_restore(script):
        seen["restore"] = script
        if soft_raises:
            raise RuntimeError("box is dead")

    async def fake_phoenix(script, files=None):
        seen["phoenix"] = script
        seen["phoenix_files"] = files
        if phoenix_raises:
            raise RuntimeError("the benchmark box did not come back")
        return "benchmark box rebuilt from image (benchmark)"

    monkeypatch.setattr(agent_module, "restore_benchmark", fake_restore)
    monkeypatch.setattr(agent_module, "phoenix_benchmark", fake_phoenix)
    return session, seen


def test_reset_soft_by_default_reverts_in_place(monkeypatch) -> None:
    session, seen = _patch_reset(monkeypatch)
    reset = reset_benchmark()

    out = anyio.run(lambda: reset(hard=False))

    assert "restore" in seen and "phoenix" not in seen
    assert session.box_method == "soft"
    assert session.box_version == 1
    assert "reset to its per-sample state" in out


def test_reset_hard_rebuilds_without_trying_soft(monkeypatch) -> None:
    session, seen = _patch_reset(monkeypatch)
    reset = reset_benchmark()

    out = anyio.run(lambda: reset(hard=True))

    assert "phoenix" in seen and "restore" not in seen
    assert session.box_method == "phoenix"
    assert session.box_version == 1
    assert "rebuilt from image" in out
    # the sample's files spec is threaded through so the rebuild can re-lay it
    assert seen["phoenix_files"] == {"benchmark:/work/f.txt": "/host/f.txt"}


def test_reset_falls_back_to_phoenix_when_soft_fails(monkeypatch) -> None:
    """A soft revert runs inside the box; if it throws, the box may be bricked."""
    session, seen = _patch_reset(monkeypatch, soft_raises=True)
    reset = reset_benchmark()

    out = anyio.run(lambda: reset(hard=False))

    assert "restore" in seen and "phoenix" in seen  # tried soft, then rebuilt
    assert session.box_method == "phoenix"
    assert "soft reset failed first" in out


def test_reset_surfaces_an_unrevivable_box_as_a_finding(monkeypatch) -> None:
    """Phoenix that cannot revive the box is a ToolError the auditor records, not a crash."""
    _patch_reset(monkeypatch, phoenix_raises=True)
    reset = reset_benchmark()

    with pytest.raises(ToolError, match="did not come back"):
        anyio.run(lambda: reset(hard=True))


def test_every_auditor_tool_is_strict_schema_valid() -> None:
    """Every auditor tool's schema must be valid under strict function-calling.

    OpenAI/Azure strict mode requires `required` to list EVERY property; a tool with
    an optional parameter is rejected with a 400 before the model runs. mockllm never
    validates this, so it must be asserted here. Guards the whole mounted set,
    mirrored tools included.
    """
    from inspect_ai.scorer import match
    from inspect_ai.tool import ToolDef, bash
    from inspect_ai.tool._tool_def import tool_defs

    from inspect_audit._agent import audit_items, auditor_tools
    from inspect_audit._contract import SolverContract

    contract = SolverContract(tools=[ToolDef(bash())])
    tools = auditor_tools(
        audit_items(), benchmark_scorers=match(), media=True, contract=contract, benchmark=True
    )
    for d in anyio.run(tool_defs, tools):
        props = set(d.parameters.properties or {})
        required = set(d.parameters.required or [])
        assert props <= required, f"{d.name}: optional params break strict mode: {props - required}"
