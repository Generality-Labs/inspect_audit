"""`grade`: the benchmark's own scorers on an edited transcript, against the live box.

The audit runs inside the benchmark's own sample, so its target, its choices and its
sandbox are the real ones. Grading is a copy of that live state with the transcript
swapped for the one under test, handed to each of the benchmark's scorers -- the
same thing Inspect's `score()` does, against scorers the audit task does not itself
run (its own scorers report verdicts).
"""

import json
from copy import deepcopy
from typing import Any

from inspect_ai.log import read_eval_log_sample
from inspect_ai.model import ChatMessage, ChatMessageAssistant, ModelName, ModelOutput
from inspect_ai.scorer import Scorer, Target
from inspect_ai.solver import TaskState
from inspect_ai.tool import Tool, ToolError, tool
from inspect_ai.util import LimitExceededError, subtask

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
    initial_store = deepcopy(dict(state.store.items()))
    initial_metadata = deepcopy(state.metadata)

    @tool
    def grade() -> Tool:
        async def execute(answer: str, eval_id: str, epoch: int) -> str:
            """Grade a submission with the benchmark's own grader, against its live box.

            With only `answer`: the benchmark's question, then `answer` as the final
            reply. With `eval_id` and `epoch`: a recorded attempt's transcript and
            completion, metadata and solver store as logged. An authored replacement
            replaces the final assistant reply and uses the initial solver store,
            so cached answers from the recorded solver cannot override your edit.
            The box is graded as it stands now:
            a fresh start unless you have changed it, never the state a recorded
            agent left it in.

            Args:
                answer: The submission. For multiple-choice items, the letter(s). With
                    a recorded attempt, "" keeps its own completion.
                eval_id: A recorded attempt's eval_id, from attempts(), or "" for none.
                epoch: That attempt's epoch, or 0 for none.
            """
            if not scorers:
                raise ToolError("This benchmark declares no scorer to grade with.")
            recorded = None
            model = state.model
            if eval_id:
                if index is None:
                    raise ToolError("No logs were declared for this audit.")
                found = index.find(sample_id, eval_id, epoch)
                model = ModelName(found.model)
                recorded = read_eval_log_sample(
                    found.log, id=found.sample_id, epoch=epoch, resolve_attachments=True
                )
                messages = deepcopy(recorded.messages)
                output = recorded.output
                provenance = "recorded"
                if answer:
                    final = next(
                        (
                            i
                            for i in range(len(messages) - 1, -1, -1)
                            if messages[i].role == "assistant"
                        ),
                        None,
                    )
                    if final is None:
                        raise ToolError("The recorded attempt has no assistant reply to replace.")
                    messages[final] = ChatMessageAssistant(content=answer)
                    output = ModelOutput.from_content(model=AUTHORED, content=answer)
                    provenance = "recorded transcript, authored answer"
            else:
                messages = [*deepcopy(their_messages), ChatMessageAssistant(content=answer)]
                output = ModelOutput.from_content(model=AUTHORED, content=answer)
                provenance = "authored"

            graded = TaskState(
                model=model,
                sample_id=recorded.id if recorded is not None else state.sample_id,
                epoch=recorded.epoch if recorded is not None else state.epoch,
                input=deepcopy(recorded.input if recorded is not None else state.input),
                messages=messages,
                target=Target(recorded.target) if recorded is not None else deepcopy(state.target),
                choices=(
                    recorded.choices if recorded is not None else [c.value for c in state.choices]
                ),
                output=deepcopy(output),
                completed=True,
                metadata=deepcopy(recorded.metadata if recorded is not None else initial_metadata),
                store=deepcopy(
                    recorded.store if recorded is not None and not answer else initial_store
                ),
                scores={},
                sample_uuid=recorded.uuid if recorded is not None else None,
            )
            if graded.choices:
                chosen = _letters(output.completion, len(graded.choices))
                for i in range(len(graded.choices)):
                    graded.choices.mark_choice(i, i in chosen)

            @subtask("grade", store=graded.store, input={"eval_id": eval_id, "epoch": epoch})
            async def score_submission() -> list[dict[str, Any]]:
                results: list[dict[str, Any]] = []
                for scorer in scorers:
                    score = await scorer(graded, graded.target)
                    if score is not None:
                        results.append(
                            {
                                "value": score.value,
                                "answer": score.answer,
                                "explanation": score.explanation,
                            }
                        )
                return results

            try:
                results = await score_submission()
            except LimitExceededError:
                raise
            except Exception as ex:
                cause: BaseException = ex
                while cause.__cause__ is not None:
                    cause = cause.__cause__
                traceback = cause.__traceback__
                while traceback is not None and traceback.tb_next is not None:
                    traceback = traceback.tb_next
                location = (
                    f" at {traceback.tb_frame.f_code.co_filename}:{traceback.tb_lineno}"
                    if traceback is not None
                    else ""
                )
                raise ToolError(
                    f"the benchmark's grader failed to run: {type(cause).__name__}: {cause}{location}"
                ) from ex
            return json.dumps({"scores": results, "graded": provenance}, default=str)

        return execute

    return grade()
