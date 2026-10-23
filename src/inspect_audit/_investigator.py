"""The investigator: a solver run in place of the benchmark's own, inside its sample.

Per sample it stages the item and the benchmark's source into the investigator's box,
binds the tools to this sample's live state -- their scorers, their boxes, the
recorded attempts at this item -- and runs a react agent with the checks in scope.
"""

import base64
import json
from collections.abc import Sequence
from copy import copy
from typing import Any

from inspect_ai.agent import AgentSubmit, as_solver, react
from inspect_ai.dataset import Sample
from inspect_ai.model import ChatMessage, ChatMessageUser, ContentImage, Model
from inspect_ai.scorer import Scorer
from inspect_ai.solver import Generate, Solver, TaskState, solver
from inspect_ai.tool import Tool, ToolDef, WebSearchProviders, bash, skill, web_search
from inspect_ai.util import sample_limits, sandbox, store_as

from . import prompts
from ._compose import INVESTIGATOR
from ._grade import grade_tool
from ._image import view_image
from ._logs import LogIndex, sliced_logs
from ._reset import reset_tool
from ._source import SourceCopy
from ._verdicts import (
    LOG_SKILLS,
    SKILLS,
    SUPPORT_SKILLS,
    AssessmentUnits,
    InvestigationItemSkill,
    record_verdict,
    submit_investigation,
)

INVESTIGATION_ROOT = "/audit"
ITEM_PROMPT = (
    "Investigate this benchmark item. It is described in /audit/item.json in your box; "
    "the benchmark's source is under /audit/benchmark/."
)
NO_BOX = (
    " This benchmark gives the evaluated agent no box of its own, so there is no "
    "benchmark_bash and no reset: grade still runs its real grader."
)


async def _write_media(messages: Sequence[ChatMessage]) -> list[Any]:
    """The item's messages as text, images written into the investigator's box."""
    rendered: list[Any] = []
    count = 0
    for message in messages:
        if isinstance(message.content, str):
            rendered.append({"role": message.role, "content": message.content})
            continue
        parts: list[Any] = []
        for content in message.content:
            if isinstance(content, ContentImage) and content.image.startswith("data:"):
                header, _, payload = content.image.partition(",")
                extension = header.split("/")[-1].split(";")[0] or "png"
                path = f"{INVESTIGATION_ROOT}/media/{count}.{extension}"
                await sandbox(INVESTIGATOR).write_file(path, base64.b64decode(payload))
                parts.append({"image": path})
                count += 1
            elif isinstance(content, ContentImage):
                parts.append({"image": content.image})
            else:
                parts.append({"text": getattr(content, "text", str(content))})
        rendered.append({"role": message.role, "content": parts})
    return rendered


async def stage_item(
    state: TaskState,
    their_sample: Sample,
    source: SourceCopy | None,
    redact: Sequence[str],
) -> None:
    """Write the item and the benchmark's source into the investigator's box."""
    box = sandbox(INVESTIGATOR)
    item = {
        "sample_id": str(state.sample_id),
        "input": await _write_media(state.messages),
        "target": state.target.target,
        "choices": [c.value for c in state.choices] if state.choices else None,
        "metadata": {k: v for k, v in (state.metadata or {}).items() if k not in redact},
        "redacted_metadata_keys": sorted(set(redact) & set(state.metadata or {})),
        "files_in_their_box": sorted((their_sample.files or {}).keys()),
        "has_setup_script": their_sample.setup is not None,
        "assessment_ids": store_as(AssessmentUnits).ids,
        "benchmark_source": source.manifest() if source is not None else None,
    }
    await box.write_file(f"{INVESTIGATION_ROOT}/item.json", json.dumps(item, indent=2, default=str))
    if source is not None:
        await box.write_file(f"{INVESTIGATION_ROOT}/benchmark.tar.gz", source.archive())
        unpacked = await box.exec(
            ["tar", "-xzf", f"{INVESTIGATION_ROOT}/benchmark.tar.gz", "-C", INVESTIGATION_ROOT]
        )
        if not unpacked.success:
            raise RuntimeError(f"could not unpack the benchmark source: {unpacked.stderr}")


def _bash(name: str, service: str | None, description: str, timeout: int) -> Tool:
    return ToolDef(
        bash(timeout=timeout, sandbox=service), name=name, description=description
    ).as_tool()


@solver
def investigator(
    *,
    checks: list[InvestigationItemSkill],
    their_scorers: list[Scorer],
    their_setup: list[Solver],
    their_samples: dict[str, Sample],
    benchmark_box: bool,
    index: LogIndex | None,
    source: SourceCopy | None,
    assessment_ids: dict[str, list[str]] | None = None,
    redact: Sequence[str] = (),
    confidential: bool = False,
    notes: str | None = None,
    search: WebSearchProviders | None = None,
    model: Model | None = None,
) -> Solver:
    """Investigate the sample this solver runs in, in place of the benchmark's solver."""
    granted = {name for check in checks for name in check.tools}
    skill_dirs = [
        str(path)
        for path in sorted(SKILLS.iterdir())
        if path.is_dir()
        and path.name in ({c.name for c in checks} | set(SUPPORT_SKILLS) | set(LOG_SKILLS))
    ]
    named = "\n".join(f"- `{c.name}`: {c.description}" for c in checks)
    system = prompts.INVESTIGATOR.format(
        items=named,
        confidential=prompts.INVESTIGATION_CONFIDENTIAL.format(root=INVESTIGATION_ROOT)
        if confidential
        else "",
        notes=prompts.INVESTIGATION_NOTES.format(notes=notes) if notes else "",
    )

    async def solve(state: TaskState, generate: Generate) -> TaskState:
        if sample_limits().cost.limit is None:
            raise RuntimeError(
                "the investigation is capped in dollars: set cost_limit on the task or the eval"
            )
        sample_id = str(state.sample_id)
        their_sample = their_samples[sample_id]
        their_messages = list(state.messages)
        store_as(AssessmentUnits).ids = list((assessment_ids or {}).get(sample_id) or [sample_id])
        await stage_item(state, their_sample, source, redact)
        if index is not None:
            # this sample's recorded attempts, as ordinary .eval files the investigator
            # reads with Inspect's log API (see the reading-logs skill)
            for name, data in sliced_logs(index, sample_id).items():
                await sandbox(INVESTIGATOR).write_file(f"{INVESTIGATION_ROOT}/logs/{name}", data)

        tools: list[Tool] = [
            _bash(
                "investigator_bash",
                INVESTIGATOR,
                "Run bash in your own box (internet, /audit).",
                300,
            ),
            skill(skill_dirs),
            record_verdict(checks),
            view_image(INVESTIGATOR),
        ]
        if search is not None:
            tools.append(web_search(providers=search))
        if their_scorers:
            tools.append(grade_tool(state, their_messages, their_scorers, index))
        if benchmark_box:
            tools.append(
                _bash(
                    "benchmark_bash",
                    None,
                    "Run bash in the benchmark's box, off the record. What you change persists.",
                    900,
                )
            )
            if "reset" in granted:
                tools.append(reset_tool(their_sample, their_setup, copy(state), generate))

        agent = react(
            name="investigator",
            description="Investigates one benchmark item and records a verdict per check.",
            prompt=system,
            tools=tools,
            model=model,
            submit=AgentSubmit(
                tool=submit_investigation(checks), name="submit", keep_in_messages=True
            ),
        )
        prompt = ITEM_PROMPT if benchmark_box else ITEM_PROMPT + NO_BOX
        state.messages = [ChatMessageUser(content=prompt)]
        return await as_solver(agent)(state, generate)

    return solve
