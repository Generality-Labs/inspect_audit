"""Inspect's checkpointing carries a crashed agent's box files, store and messages over.

The investigator's own workspace sync and resume exist to survive a crash. This
pins the behaviour of Inspect's checkpointing that would replace them: a react
agent writes a file in its box and a value in the store, then a tool crashes the
sample; `eval_set`'s retry -- what Hawk's runner does -- must come back with the file, the value and the transcript,
and must not replay the turns it already took.
"""

from pathlib import Path
from typing import Any

import pytest
from inspect_ai import Task, eval_set
from inspect_ai.agent import react
from inspect_ai.dataset import Sample
from inspect_ai.log import read_eval_log
from inspect_ai.model import (
    ChatMessageAssistant,
    GenerateConfig,
    ModelAPI,
    ModelOutput,
    get_model,
    modelapi,
)
from inspect_ai.scorer import Score, Target, scorer
from inspect_ai.solver import TaskState
from inspect_ai.tool import ToolCall, ToolChoice, ToolInfo, bash, tool
from inspect_ai.util import (
    ArchiveSnapshots,
    CheckpointConfig,
    SandboxSnapshotConfig,
    TurnInterval,
    store,
)

pytestmark = pytest.mark.docker

COMPOSE = """services:
  default:
    image: python:3.12-slim
    command: sh -c "mkdir -p /workspace && exec tail -f /dev/null"
    init: true
    network_mode: none
"""

# the script the fake model follows, chosen by how many turns the transcript holds,
# so a resumed agent picks up at the turn after its last checkpoint
SCRIPT = [
    ("bash", {"command": "mkdir -p /workspace && echo written-before-crash > /workspace/a.txt"}),
    ("remember", {"value": "stored-before-crash"}),
    ("crash_once", {}),
    ("bash", {"command": "cat /workspace/a.txt"}),
    ("submit", {"answer": "done"}),
]


@modelapi(name="scripted")
class Scripted(ModelAPI):
    def __init__(self, model_name: str, **kwargs: Any) -> None:
        super().__init__(model_name)

    async def generate(
        self,
        input: list[Any],
        tools: list[ToolInfo],
        tool_choice: ToolChoice,
        config: GenerateConfig,
    ) -> ModelOutput:
        turn = sum(isinstance(m, ChatMessageAssistant) for m in input)
        name, args = SCRIPT[turn]
        call = ToolCall(id=f"call-{turn}", function=name, arguments=args)
        return ModelOutput.for_tool_call(self.model_name, name, args, tool_call_id=call.id)


@tool
def remember():
    async def execute(value: str) -> str:
        """Keep a value in the sample store.

        Args:
            value: the value.
        """
        store().set("remembered", value)
        return "ok"

    return execute


@tool
def crash_once(marker: Path):
    async def execute() -> str:
        """Crash the sample the first time it is called."""
        if not marker.exists():
            marker.write_text("crashed")
            raise RuntimeError("simulated runner death")
        return "survived"

    return execute


@scorer(metrics=[])
def carried_over():
    async def score(state: TaskState, target: Target) -> Score:
        from inspect_ai.util import sandbox

        calls = [
            c.function
            for m in state.messages
            if isinstance(m, ChatMessageAssistant)
            for c in m.tool_calls or []
        ]
        tool_outputs = [m.text for m in state.messages if m.role == "tool"]
        file_now = await sandbox().exec(["cat", "/workspace/a.txt"])
        return Score(
            value=1,
            metadata={
                "calls": calls,
                "tool_outputs": tool_outputs,
                "file_now": file_now.stdout.strip(),
                "store": store().get("remembered"),
            },
        )

    return score


@pytest.mark.parametrize("strategy", ["archive", "restic"])
def test_a_crashed_agent_resumes_with_its_box_store_and_transcript(
    tmp_path: Path, strategy: str
) -> None:
    (tmp_path / "compose.yaml").write_text(COMPOSE)
    paths: Any = (
        SandboxSnapshotConfig(paths=["/workspace"], strategy=ArchiveSnapshots())
        if strategy == "archive"
        else {"default": ["/workspace"]}
    )
    task = Task(
        dataset=[Sample(input="go", id="s1")],
        solver=react(tools=[bash(), remember(), crash_once(tmp_path / "crashed")]),
        scorer=carried_over(),
        sandbox=("docker", str(tmp_path / "compose.yaml")),
        checkpoint=CheckpointConfig(
            trigger=TurnInterval(every=1),
            sandbox_paths={"default": paths} if strategy == "archive" else paths,
            max_consecutive_failures=0,
        ),
    )
    model = get_model("scripted/s")
    ok, logs = eval_set(
        task,
        model=model,
        log_dir=str(tmp_path / "logs"),
        retry_attempts=1,
        retry_wait=0,
        display="none",
    )
    assert (tmp_path / "crashed").exists(), "the first attempt should die in crash_once"
    [second] = logs
    assert ok and second.status == "success", second.error
    [sample] = read_eval_log(second.location).samples or []
    meta = sample.scores["carried_over"].metadata or {}
    assert meta["file_now"] == "written-before-crash"
    assert meta["store"] == "stored-before-crash"
    # the turns before the crash are not replayed: one bash write, one remember
    assert meta["calls"].count("remember") == 1, meta["calls"]
    assert "written-before-crash" in meta["tool_outputs"][-1], meta["tool_outputs"]
