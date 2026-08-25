"""The benchmark's own session, reconstructed for the auditor to handle.

An audit reconstructs every layer of the original eval -- its environment, its
metadata, its grader -- except the session itself. This module adds that
counterpart: a `BenchmarkState` held in the typed store, representing the
evaluated agent's conversation as an object the auditor manipulates through
the `attempt` tool and the grader consumes. The auditor lives in its own
`TaskState`; it only ever *handles* this one. The invariant enforced in
`_agent.grade_benchmark` is the mirror of the sandbox isolation: the
benchmark's scorer sees the benchmark's state, never the audit's.

Every message carries provenance, because verdict strength reads off it:

- `real`     -- loaded verbatim from a recorded attempt, or the item's own input
- `enacted`  -- produced by actually executing against the benchmark box
- `authored` -- written by the auditor, including elicitation reconstructed
                from the declared prompt template (an approximation, so it
                does not get to claim `real`)

Assistant turns may be authored freely: a real model could have said anything,
so authored assistant content explores only the grader's reachable domain. An
authored *tool result* is different -- the environment never said it -- which
is why grades stamp the full mix rather than a single flag.
"""

import json
import tempfile
from pathlib import Path
from typing import Literal

from inspect_ai.log import EvalSample, read_eval_log_samples
from inspect_ai.model import (
    ChatMessage,
    ChatMessageAssistant,
    ChatMessageSystem,
    ChatMessageTool,
    ChatMessageUser,
    ModelOutput,
)
from inspect_ai.solver import TaskState
from inspect_ai.solver._task_state import sample_state
from inspect_ai.tool import Tool, ToolCall, ToolError, tool
from inspect_ai.util import StoreModel, sandbox, store_as
from pydantic import BaseModel, Field, JsonValue

Provenance = Literal["real", "enacted", "authored"]

AUTHORED_MODEL = "inspect_audit/authored"


class AttemptMessage(BaseModel):
    """One message of the reconstructed session, with how it got there."""

    provenance: Provenance
    message: ChatMessage


class BenchmarkState(StoreModel):
    """The benchmark session under reconstruction, one per audit sample."""

    seeded: str | None = None
    """Receipt for where the session came from: `new`, or a log slice."""

    messages: list[AttemptMessage] = Field(default_factory=list)
    output: ModelOutput | None = None
    attempt_store: dict[str, JsonValue] = Field(default_factory=dict)
    completed: bool = False
    box_version: int = 0
    """Bumped by `reset`: which state of the benchmark box grades ran against."""

    def chat_messages(self) -> list[ChatMessage]:
        """The session as plain messages, for a grader or a `TaskState`."""
        return [m.message for m in self.messages]

    def provenance_mix(self) -> dict[str, int]:
        """How synthetic the session is, as provenance -> message count."""
        mix: dict[str, int] = {}
        for m in self.messages:
            mix[m.provenance] = mix.get(m.provenance, 0) + 1
        return mix


def seed_new(state: BenchmarkState, input: JsonValue, prompt: str | None) -> None:
    """Seed the session at t=0: the item's own input, and any declared prompt.

    The input is the benchmark's own record, so it is `real`. The system
    message comes from the declared prompt *template*, not a recorded
    instantiation, so it is honestly `authored`.

    Args:
        state: The state to seed, replacing anything already there.
        input: The original sample's input: a string, or serialised messages.
        prompt: The declared prompt template, where one was recovered.
    """
    messages: list[AttemptMessage] = []
    if prompt:
        messages.append(
            AttemptMessage(provenance="authored", message=ChatMessageSystem(content=prompt))
        )
    if isinstance(input, str):
        messages.append(
            AttemptMessage(provenance="real", message=ChatMessageUser(content=input))
        )
    elif isinstance(input, list):
        for item in input:
            messages.append(AttemptMessage.model_validate({"provenance": "real", "message": item}))
    else:
        raise ValueError(f"cannot seed from input of type {type(input).__name__}")
    state.seeded = "new"
    state.messages = messages
    state.output = None
    state.completed = False


def seed_from_sample(state: BenchmarkState, sample: EvalSample, source: str) -> None:
    """Seed the session from a recorded attempt, verbatim.

    Args:
        state: The state to seed, replacing anything already there.
        sample: The recorded attempt, read from a sliced log.
        source: Receipt for where the attempt came from (log name and epoch).
    """
    state.seeded = source
    state.messages = [
        AttemptMessage(provenance="real", message=message) for message in sample.messages
    ]
    state.output = sample.output
    state.attempt_store = dict(sample.store or {})
    state.completed = True


def append_message(
    state: BenchmarkState,
    role: str,
    content: str,
    *,
    provenance: Provenance = "authored",
    tool_call_id: str | None = None,
    tool_calls: str | None = None,
) -> None:
    """Append one message to the session.

    Args:
        state: The state to append to.
        role: One of `system`, `user`, `assistant`, `tool`.
        content: The message content.
        provenance: How the message came to be (`authored` unless enacted).
        tool_call_id: For a `tool` message: the call it answers.
        tool_calls: For an `assistant` message: tool calls as a JSON list of
            `{id, function, arguments}` objects.
    """
    message: ChatMessage
    if role == "system":
        message = ChatMessageSystem(content=content)
    elif role == "user":
        message = ChatMessageUser(content=content)
    elif role == "assistant":
        calls = None
        if tool_calls:
            try:
                parsed = json.loads(tool_calls)
                calls = [
                    ToolCall(
                        id=str(c["id"]),
                        function=str(c["function"]),
                        arguments=dict(c.get("arguments") or {}),
                    )
                    for c in parsed
                ]
            except (ValueError, TypeError, KeyError) as ex:
                raise ValueError(
                    "tool_calls must be a JSON list of {id, function, arguments} "
                    f"objects: {ex}"
                ) from None
        message = ChatMessageAssistant(content=content, tool_calls=calls, model=AUTHORED_MODEL)
    elif role == "tool":
        if not tool_call_id:
            raise ValueError("a tool message needs the tool_call_id it answers")
        message = ChatMessageTool(content=content, tool_call_id=tool_call_id)
    else:
        raise ValueError(f"unknown role {role!r}: expected system, user, assistant or tool")
    state.messages = [*state.messages, AttemptMessage(provenance=provenance, message=message)]


def edit_message(state: BenchmarkState, index: int, content: str) -> None:
    """Replace one message's content; the edit marks it `authored`."""
    if not 0 <= index < len(state.messages):
        raise ValueError(f"index {index} out of range: session has {len(state.messages)} messages")
    edited = state.messages[index].message.model_copy(update={"content": content})
    state.messages = [
        *state.messages[:index],
        AttemptMessage(provenance="authored", message=edited),
        *state.messages[index + 1 :],
    ]


def truncate_messages(state: BenchmarkState, index: int) -> None:
    """Drop the session from `index` on."""
    if not 0 <= index <= len(state.messages):
        raise ValueError(f"index {index} out of range: session has {len(state.messages)} messages")
    state.messages = state.messages[:index]
    state.output = None
    state.completed = False


def complete_attempt(state: BenchmarkState, answer: str) -> None:
    """Fix the attempt's final answer and end it.

    The universal terminal: every attempt, whatever its tool vocabulary, ends
    by fixing a completion (possibly empty, when the answer is the state of
    the box). Appends the answer as an authored assistant turn so a
    transcript-reading scorer sees what a submitting agent's transcript shows.
    """
    if answer:
        append_message(state, "assistant", answer)
    state.output = ModelOutput.from_content(model=AUTHORED_MODEL, content=answer)
    state.completed = True


def receipt(state: BenchmarkState) -> str:
    """The session's current shape, as the tool's return value."""
    return json.dumps(
        {
            "seeded": state.seeded,
            "messages": len(state.messages),
            "provenance": state.provenance_mix(),
            "completed": state.completed,
            "box_version": state.box_version,
        }
    )


def benchmark_task_state(
    current: TaskState, session: BenchmarkState, answer: str
) -> TaskState:
    """The benchmark's own `TaskState`, for its grader to judge.

    Built from the benchmark's side of everything -- its input, choices and
    metadata carried on the audit sample, and the reconstructed session --
    the way `inspect score` rebuilds states from a log. The audit's own
    state supplies nothing but the target and the model name: a grader
    reading the question, the transcript or the store must see the
    benchmark's, never the audit's.

    Args:
        current: The audit's `TaskState` (for target and ids).
        session: The reconstructed benchmark session.
        answer: The submission under grade, as `output.completion`. Empty
            grades the benchmark environment exactly as it stands.
    """
    metadata = current.metadata or {}
    raw_input = metadata.get("benchmark_input") or ""
    input_messages: str | list[ChatMessage]
    if isinstance(raw_input, list):
        input_messages = [
            AttemptMessage.model_validate({"provenance": "real", "message": m}).message
            for m in raw_input
        ]
    else:
        input_messages = str(raw_input)
    item = metadata.get("audit_item") or {}
    return TaskState(
        model=current.model,
        sample_id=item.get("sample_id", current.sample_id),
        epoch=current.epoch,
        input=input_messages,
        target=current.target,
        choices=metadata.get("benchmark_choices"),
        messages=session.chat_messages(),
        output=ModelOutput.from_content(model=AUTHORED_MODEL, content=answer),
        completed=True,
        metadata=dict(metadata.get("benchmark_metadata") or {}),
        store=dict(session.attempt_store),
    )


ATTEMPT_DIR = "attempt"


async def mirror_state(state: BenchmarkState, root: str) -> None:
    """Write the session into the auditor's box, so reading it is just files.

    Args:
        state: The session to mirror.
        root: The cell root (`/audit`).
    """
    await sandbox().write_file(
        f"{root}/{ATTEMPT_DIR}/messages.json",
        json.dumps([m.model_dump(exclude_none=True) for m in state.messages], indent=1, default=str),
    )
    await sandbox().write_file(f"{root}/{ATTEMPT_DIR}/state.json", receipt(state))


@tool
def attempt(root: str, prompt: str | None = None) -> Tool:
    """The reconstructed benchmark session, as a tool.

    Args:
        root: The cell root (`/audit`).
        prompt: The benchmark's declared prompt template, for `new`.
    """

    async def execute(
        command: str,
        log: str | None = None,
        epoch: int | None = None,
        role: str | None = None,
        content: str | None = None,
        tool_call_id: str | None = None,
        tool_calls: str | None = None,
        index: int | None = None,
        answer: str | None = None,
    ) -> str:
        """Build and edit the benchmark session that `grade` will judge.

        This is the evaluated agent's session as an object: seed it, write
        into it, end it, then grade it. It is mirrored under `attempt/` in
        the cell after every change, so read it with your shell. Every
        message records its provenance -- `real` (from a recorded attempt),
        `enacted` (actually executed against the benchmark box), `authored`
        (written by you) -- and grades are stamped with the mix, so a
        verdict's strength is legible from how synthetic its evidence was.

        Commands:
          new       start the session at t=0: the item's own input, plus the
                    benchmark's declared system prompt where one was recovered
          load      seed from a recorded attempt: `log` (filename as it appears
                    in logs/), and `epoch` when the log holds more than one
          append    add a message: `role` and `content`, plus `tool_calls`
                    (JSON list of {id, function, arguments}) on an assistant
                    message or `tool_call_id` on a tool message
          edit      replace the `content` of the message at `index`
          truncate  drop the session from `index` on (counterfactuals: load a
                    real attempt, truncate at the turn under test, rebuild)
          complete  fix the final `answer` and end the attempt; pass an empty
                    answer when the submission is the state of the box

        Args:
            command: One of `new`, `load`, `append`, `edit`, `truncate`, `complete`.
            log: For `load`: the log's filename, exactly as it appears in `logs/`.
            epoch: For `load`: the attempt's epoch (defaults to 1).
            role: For `append`: `system`, `user`, `assistant`, or `tool`.
            content: For `append` and `edit`: the message content.
            tool_call_id: For `append` of a `tool` message: the call it answers.
            tool_calls: For `append` of an `assistant` message: tool calls as a
                JSON list of `{id, function, arguments}` objects.
            index: For `edit` and `truncate`: the message position, 0-based.
            answer: For `complete`: the attempt's final answer.
        """
        state = store_as(BenchmarkState)
        try:
            if command == "new":
                current = sample_state()
                metadata = (current.metadata or {}) if current is not None else {}
                if "benchmark_input" not in metadata:
                    raise ValueError("this item carries no original input to seed from")
                seed_new(state, metadata["benchmark_input"], prompt)
            elif command == "load":
                if not log:
                    raise ValueError("load needs the log filename, as it appears in logs/")
                sample = await _read_sliced(root, log, epoch or 1)
                seed_from_sample(state, sample, source=f"{log}#epoch={epoch or 1}")
            elif command == "append":
                if not role or content is None:
                    raise ValueError("append needs a role and content")
                append_message(
                    state, role, content, tool_call_id=tool_call_id, tool_calls=tool_calls
                )
            elif command == "edit":
                if index is None or content is None:
                    raise ValueError("edit needs an index and the replacement content")
                edit_message(state, index, content)
            elif command == "truncate":
                if index is None:
                    raise ValueError("truncate needs the index to cut from")
                truncate_messages(state, index)
            elif command == "complete":
                complete_attempt(state, answer or "")
            else:
                raise ValueError(
                    f"unknown command {command!r}: expected new, load, append, "
                    "edit, truncate or complete"
                )
        except ValueError as ex:
            raise ToolError(str(ex)) from None
        await mirror_state(state, root)
        return receipt(state)

    return execute


async def _read_sliced(root: str, log: str, epoch: int) -> EvalSample:
    """Read one attempt from a sliced log in the cell.

    The cell is the source of truth, so the log is read out of the container
    rather than from host bookkeeping.
    """
    if "/" in log:
        raise ToolError("give the log's filename alone, as it appears in logs/")
    try:
        data = await sandbox().read_file(f"{root}/logs/{log}", text=False)
    except Exception as ex:
        raise ToolError(f"could not read logs/{log}: {ex}") from None
    assert isinstance(data, bytes)
    with tempfile.TemporaryDirectory(prefix="inspect_audit_attempt_") as staging:
        host = Path(staging) / log
        host.write_bytes(data)
        for sample in read_eval_log_samples(str(host), all_samples_required=False):
            if sample.epoch == epoch:
                return sample
    raise ToolError(f"no attempt with epoch {epoch} in logs/{log}")
