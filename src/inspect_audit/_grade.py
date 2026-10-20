"""`grade`: the benchmark's own scorers on an edited transcript, against the live box.

The audit runs inside the benchmark's own sample, so its target, its choices and its
sandbox are the real ones. Grading is a copy of that live state with the transcript
swapped for the one under test, handed to each of the benchmark's scorers -- the
same thing Inspect's `score()` does, against scorers the audit task does not itself
run (its own scorers report verdicts).
"""

import json
from copy import copy
from typing import Any

from inspect_ai.log import read_eval_log_sample
from inspect_ai.model import ChatMessage, ChatMessageAssistant, ModelOutput
from inspect_ai.scorer import Scorer
from inspect_ai.solver import TaskState
from inspect_ai.tool import Tool, ToolError, tool
from inspect_ai.util import LimitExceededError

from ._logs import LogIndex

AUTHORED = "inspect_audit/authored"


def _letters(answer: str, count: int) -> list[int]:
    """Choice indexes named by an answer like `B` or `A, C`, or a refusal."""
    letters = [part.strip().upper() for part in answer.replace(";", ",").split(",") if part.strip()]
    indexes = [ord(letter) - ord("A") for letter in letters if len(letter) == 1]
    if not letters or len(indexes) != len(letters) or not all(0 <= i < count for i in indexes):
        raise ToolError(
            f"This item has {count} choices: grade it with the letter(s) you choose "
            f"(e.g. 'B' or 'A, C'), not {answer!r}."
        )
    return indexes


def grade_tool(
    state: TaskState,
    their_messages: list[ChatMessage],
    scorers: list[Scorer],
    index: LogIndex | None,
) -> Tool:
    """`grade` bound to one audited sample."""
    sample_id = str(state.sample_id)

    @tool
    def grade() -> Tool:
        async def execute(answer: str = "", eval_id: str = "", epoch: int = 0) -> str:
            """Grade a submission with the benchmark's own grader, against its live box.

            With only `answer`: the benchmark's question, then `answer` as the final
            reply. With `eval_id` and `epoch`: a recorded attempt's transcript and
            completion as they were logged -- and if you also pass `answer`, that
            replaces the recorded completion. The box is graded as it stands now:
            a fresh start unless you have changed it, never the state a recorded
            agent left it in.

            Args:
                answer: The submission. For multiple-choice items, the letter(s).
                eval_id: A recorded attempt's eval_id, from attempts().
                epoch: That attempt's epoch.
            """
            if not scorers:
                raise ToolError("This benchmark declares no scorer to grade with.")
            if eval_id:
                if index is None:
                    raise ToolError("No logs were declared for this audit.")
                found = index.find(sample_id, eval_id, epoch)
                recorded = read_eval_log_sample(
                    found.log, id=found.sample_id, epoch=epoch, resolve_attachments=True
                )
                messages = list(recorded.messages)
                output = recorded.output
                provenance = "recorded"
                if answer:
                    messages.append(ChatMessageAssistant(content=answer))
                    output = ModelOutput.from_content(model=AUTHORED, content=answer)
                    provenance = "recorded transcript, authored answer"
            else:
                messages = [*their_messages, ChatMessageAssistant(content=answer)]
                output = ModelOutput.from_content(model=AUTHORED, content=answer)
                provenance = "authored"

            graded = copy(state)
            graded.messages = messages
            graded.output = output
            marks: list[bool | None] = []
            if state.choices:
                marks = [choice.correct for choice in state.choices]
                chosen = _letters(output.completion, len(state.choices))
                for i in range(len(state.choices)):
                    graded.choices.mark_choice(i, i in chosen)

            results: list[dict[str, Any]] = []
            try:
                for scorer in scorers:
                    score = await scorer(graded, state.target)
                    if score is not None:
                        results.append(
                            {
                                "value": score.value,
                                "answer": score.answer,
                                "explanation": score.explanation,
                            }
                        )
            except LimitExceededError:
                raise
            except Exception as ex:
                cause: BaseException = ex
                while cause.__cause__ is not None:
                    cause = cause.__cause__
                raise ToolError(
                    f"the benchmark's grader failed to run: {type(cause).__name__}: {cause}"
                ) from ex
            finally:
                # graded shares the live state's choices: put the marks back
                for choice, mark in zip(state.choices, marks, strict=False):
                    choice.correct = mark
            return json.dumps({"scores": results, "graded": provenance}, default=str)

        return execute

    return grade()
