"""One benchmark item, and the filesystem an auditor gets to judge it with.

An item is one sample of the audited task, together with every recorded attempt at
it. Many attempts inform one item: the attempts are evidence, not the thing being
judged.

The audited task's own packages are installed in the sandbox, so the benchmark's code
is read in place rather than copied:

    /audit/sample.json          the sample, in Inspect's own shape
    /audit/logs/<name>.eval     real logs, headers verbatim, sliced to this item
    /audit/env/<Dockerfile>     the environment's own definition, as declared
    /audit/gold/grading.md      where grading lives, and how to read it

Values in `Sample.files` are host paths, never file contents. Inspect resolves a
contents-shaped value as a data URI, then an HTTP GET, then an existing file at that
path, so a target of "pyproject.toml" is replaced by that file and a URL-shaped
target is fetched from the web.
"""

import json
from pathlib import Path
from typing import Any

from inspect_ai import Task
from inspect_ai.dataset import Sample
from inspect_ai.util import SandboxEnvironmentType
from pydantic import BaseModel, Field

__all__ = ["AUDIT_ROOT", "AttemptRef", "AuditItem", "item_files", "item_sample"]

AUDIT_ROOT = "/audit"


class AttemptRef(BaseModel):
    """One recorded attempt at an item: where it lives, and how it scored.

    A pointer, not a copy. The attempt itself — answer, transcript, score, and the
    header saying how it was graded — is in the sliced log under `/audit/logs`.
    """

    model: str
    epoch: int
    scores: dict[str, str] = Field(default_factory=dict)
    """What each scorer gave this attempt, keyed by scorer name.

    A dict rather than one value: a task can run several scorers, and which of them
    decides "solved" is the auditor's question, not ours to collapse.
    """

    log_file: str
    sample_id: str | int


class AuditItem(BaseModel):
    """Which item an audit sample is auditing, carried in its `Sample.metadata`."""

    task: str
    task_args: dict[str, Any] = Field(default_factory=dict)
    sample_id: str | int
    attempts: list[AttemptRef] = Field(default_factory=list)


def item_files(
    task: Task,
    sample: Sample,
    attempts: list[AttemptRef],
    *,
    stage: Path,
    sandbox: SandboxEnvironmentType | None = None,
) -> dict[str, str]:
    """Build the `Sample.files` mapping for one item, staging each file on the host.

    Args:
        task: The task being audited.
        sample: The sample being audited.
        attempts: The recorded attempts at this sample.
        stage: Directory to stage this item's files in.
        sandbox: The sandbox the item will run in, whose definition is staged so the
            auditor can read how its environment was built.

    Returns:
        Mapping of sandbox path to host path.
    """
    from ._gold import grading_doc
    from ._sandbox import env_files
    from ._slice import sample_logs

    stage.mkdir(parents=True, exist_ok=True)
    files: dict[str, str] = {}

    def staged(name: str, content: str) -> None:
        host = stage / name
        host.parent.mkdir(parents=True, exist_ok=True)
        host.write_text(content, encoding="utf-8")
        files[f"{AUDIT_ROOT}/{name}"] = str(host)

    # A one-record dataset in Inspect's own shape, so the auditor can rebuild the item
    # exactly as the benchmark defines it rather than trusting a rendering of ours.
    staged("sample.json", json.dumps([_record(sample)], indent=2, default=str))
    staged("gold/grading.md", grading_doc(task, sample))

    files.update(env_files(sandbox, stage=stage / "env"))
    files.update(sample_logs(attempts, stage=stage / "logs"))
    return files


def _record(sample: Sample) -> dict[str, Any]:
    """A sample as a plain record, under Inspect's own field names."""
    return dict(sample.model_dump(exclude_none=True, exclude={"files", "sandbox", "setup"}))


def item_sample(
    task: Task,
    sample: Sample,
    item: AuditItem,
    *,
    prompt: str,
    stage: Path,
    sandbox: SandboxEnvironmentType | None = None,
) -> Sample:
    """One audited item, as an Inspect `Sample`."""
    return Sample(
        id=str(item.sample_id),
        input=prompt,
        target=sample.target,
        metadata={"audit_item": item.model_dump()},
        sandbox=sandbox,
        files=item_files(task, sample, item.attempts, stage=stage, sandbox=sandbox),
    )
