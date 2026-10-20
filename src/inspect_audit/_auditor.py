"""The auditor: a solver run in place of the benchmark's own, inside its sample.

Per sample it stages the item and the benchmark's source into the auditor's box,
binds the tools to this sample's live state -- their scorers, their boxes, the
recorded attempts at this item -- and runs a react agent with the checks in scope.
"""

import base64
import io
import json
import tarfile
from collections.abc import Sequence
from io import BytesIO
from pathlib import Path
from typing import Any

from inspect_ai.agent import AgentState, AgentSubmit, react
from inspect_ai.dataset import Sample
from inspect_ai.model import ChatMessage, ChatMessageUser, ContentImage
from inspect_ai.scorer import Scorer
from inspect_ai.solver import Generate, Solver, TaskState, solver
from inspect_ai.tool import Tool, ToolDef, ToolError, ToolResult, bash, skill, tool
from inspect_ai.util import sandbox, store_as
from PIL import Image

from . import prompts
from ._compose import AUDITOR
from ._grade import grade_tool
from ._logs import LogIndex, log_tools
from ._reset import reset_tool
from ._verdicts import (
    SKILLS,
    SUPPORT_SKILLS,
    AssessmentUnits,
    AuditItemSkill,
    record_verdict,
    submit_audit,
)

AUDIT_ROOT = "/audit"
ITEM_PROMPT = (
    "Audit this benchmark item. It is described in /audit/item.json in your box; "
    "the benchmark's source is under /audit/benchmark/."
)
MAX_SOURCE_BYTES = 100 * 1024 * 1024


def source_archive(source: Path) -> bytes:
    """The benchmark's source directory as a tar, refusing anything oversized."""
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        archive.add(
            source,
            arcname="benchmark",
            filter=lambda info: None if "__pycache__" in info.name else info,
        )
    data = buffer.getvalue()
    if len(data) > MAX_SOURCE_BYTES:
        raise ValueError(
            f"the benchmark source at {source} is {len(data) // 2**20} MiB compressed; "
            "declare a narrower source="
        )
    return data


async def _write_media(messages: Sequence[ChatMessage]) -> list[Any]:
    """The item's messages as text, images written into the auditor's box."""
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
                path = f"{AUDIT_ROOT}/media/{count}.{extension}"
                await sandbox(AUDITOR).write_file(path, base64.b64decode(payload))
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
    archive: bytes | None,
    redact: Sequence[str],
) -> None:
    """Write the item and the benchmark's source into the auditor's box."""
    box = sandbox(AUDITOR)
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
    }
    await box.write_file(f"{AUDIT_ROOT}/item.json", json.dumps(item, indent=2, default=str))
    if archive is not None:
        await box.write_file(f"{AUDIT_ROOT}/benchmark.tar.gz", archive)
        unpacked = await box.exec(
            ["tar", "-xzf", f"{AUDIT_ROOT}/benchmark.tar.gz", "-C", AUDIT_ROOT]
        )
        if not unpacked.success:
            raise RuntimeError(f"could not unpack the benchmark source: {unpacked.stderr}")


@tool
def view_image() -> Tool:
    async def execute(path: str) -> ToolResult:
        """Look at an image in your box: the item's media, or a figure you drew.

        Reading the bytes with bash establishes nothing; call this to see the picture.

        Args:
            path: The image's path in your box.
        """
        try:
            data = await sandbox(AUDITOR).read_file(path, text=False)
        except Exception as ex:
            raise ToolError(f"Could not read {path!r}: {type(ex).__name__}: {ex}") from None
        try:
            with Image.open(BytesIO(data)) as preview:
                image_format = preview.format
                preview.verify()
        except Exception as ex:
            raise ToolError(f"Cannot preview {path!r}: {ex}; export a PNG") from None
        mime = {"PNG": "image/png", "JPEG": "image/jpeg", "GIF": "image/gif", "WEBP": "image/webp"}
        if (image_format or "") not in mime:
            raise ToolError(f"Unsupported image format {image_format}; export a PNG")
        encoded = base64.b64encode(data).decode()
        return [ContentImage(image=f"data:{mime[image_format or '']};base64,{encoded}")]

    return execute


def _bash(name: str, service: str | None, description: str, timeout: int) -> Tool:
    return ToolDef(
        bash(timeout=timeout, sandbox=service), name=name, description=description
    ).as_tool()


@solver
def auditor(
    *,
    checks: list[AuditItemSkill],
    their_scorers: list[Scorer],
    their_setup: list[Solver],
    their_samples: dict[str, Sample],
    index: LogIndex | None,
    source: Path | None,
    assessment_ids: dict[str, list[str]] | None = None,
    redact: Sequence[str] = (),
    confidential: bool = False,
    notes: str | None = None,
) -> Solver:
    """Audit the sample this solver runs in, in place of the benchmark's solver."""
    archive = source_archive(source) if source is not None else None
    granted = {name for check in checks for name in check.tools}
    skill_dirs = [
        str(path)
        for path in sorted(SKILLS.iterdir())
        if path.is_dir() and path.name in ({c.name for c in checks} | set(SUPPORT_SKILLS))
    ]
    named = "\n".join(f"- `{c.name}`: {c.description}" for c in checks)
    system = prompts.AUDITOR.format(
        items=named,
        confidential=prompts.AUDIT_CONFIDENTIAL.format(root=AUDIT_ROOT) if confidential else "",
        notes=prompts.AUDIT_NOTES.format(notes=notes) if notes else "",
    )

    async def solve(state: TaskState, generate: Generate) -> TaskState:
        sample_id = str(state.sample_id)
        their_sample = their_samples[sample_id]
        their_messages = list(state.messages)
        store_as(AssessmentUnits).ids = list((assessment_ids or {}).get(sample_id) or [sample_id])
        await stage_item(state, their_sample, archive, redact)

        tools: list[Tool] = [
            _bash("auditor_bash", AUDITOR, "Run bash in your own box (internet, /audit).", 300),
            _bash(
                "benchmark_bash",
                None,
                "Run bash in the benchmark's box, off the record. What you change persists.",
                900,
            ),
            skill(skill_dirs),
            record_verdict(checks),
            view_image(),
            *log_tools(index, sample_id),
        ]
        if their_scorers:
            tools.append(grade_tool(state, their_messages, their_scorers, index))
        if "reset" in granted:
            tools.append(reset_tool(their_sample, their_setup, state, generate))

        agent = react(
            name="auditor",
            description="Audits one benchmark item and records a verdict per check.",
            prompt=system,
            tools=tools,
            submit=AgentSubmit(tool=submit_audit(checks), name="submit", keep_in_messages=True),
        )
        result = await agent(AgentState(messages=[ChatMessageUser(content=ITEM_PROMPT)]))
        state.messages = result.messages
        state.output = result.output
        return state

    return solve
