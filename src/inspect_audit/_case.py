"""The audit case: what one item's audit filesystem contains, and how it is built.

A case is one benchmark item — one sample, and every recorded attempt at it. Many
attempts inform one item; the attempts are evidence, not the thing being judged.

The filesystem is deliberately thin, and nothing in it is a summary we invented:

    /audit/sample.json          the sample, in Inspect's own shape
    /audit/logs/<name>.eval     real logs, headers verbatim, sliced to this item
    /audit/gold/grading.md      where grading lives and how to read it
    /audit/case.json            provenance: how this case was assembled

The benchmark's own code is not copied either — the task's packages are installed in
the sandbox, so the auditor reads the real source in place.

Every value in `Sample.files` is a host **path**, never file contents. Inspect
resolves a contents-shaped value by trying, in order, a data URI, an HTTP GET, and
then an existing file at that path — so a gold that happens to look like a path or a
URL is silently replaced by that file, or fetched from the web. Paths are
unambiguous.
"""

import json
from pathlib import Path
from typing import Any

from inspect_ai import Task
from inspect_ai.dataset import Sample
from inspect_ai.util import SandboxEnvironmentType
from pydantic import BaseModel, Field

__all__ = ["AUDIT_ROOT", "AttemptRef", "CaseSpec", "case_sample", "materialise"]

AUDIT_ROOT = "/audit"


class AttemptRef(BaseModel):
    """One recorded attempt at an item: where it lives, and how it was graded.

    This is a pointer, not a copy. The attempt itself — answer, transcript, score,
    and the header that says how it was graded — is in the sliced log under
    `/audit/logs`, which is a real `.eval` the auditor opens with `read_eval_log`.
    """

    model: str
    epoch: int
    score: str | float | bool | None = None
    log_file: str
    sample_id: str | int


class CaseSpec(BaseModel):
    """Everything needed to rebuild a case, carried in `Sample.metadata`.

    The audit's provenance record: which task, resolved with which arguments, which
    sample, which attempts were in scope, and a hash of the sample input recorded so
    a later reader can tell whether the dataset has moved since.
    """

    task: str
    task_args: dict[str, Any] = Field(default_factory=dict)
    sample_id: str | int
    input_hash: str
    attempts: list[AttemptRef] = Field(default_factory=list)


def materialise(
    task: Task,
    sample: Sample,
    case_spec: CaseSpec,
    *,
    stage: Path,
) -> dict[str, str]:
    """Build the `Sample.files` mapping for a case, staging each file on the host.

    Args:
        task: The task being audited.
        sample: The sample being audited.
        case_spec: Provenance for this case, including the attempts in scope.
        stage: Directory to stage this case's files in.

    Returns:
        Mapping of sandbox path -> host path.
    """
    from ._gold import grading_doc
    from ._slice import sample_logs

    stage.mkdir(parents=True, exist_ok=True)
    files: dict[str, str] = {}

    def staged(name: str, content: str) -> None:
        host = stage / name
        host.parent.mkdir(parents=True, exist_ok=True)
        host.write_text(content, encoding="utf-8")
        files[f"{AUDIT_ROOT}/{name}"] = str(host)

    # The sample in Inspect's own shape, as a one-record dataset: `input`, `target`,
    # `metadata` and anything else the benchmark carries, under the names Inspect
    # uses. Loadable with `json_dataset()`, so the auditor can reconstruct the item
    # exactly as the benchmark presents it rather than trusting a rendering of ours.
    staged("sample.json", json.dumps([_sample_record(sample)], indent=2, default=str))
    staged("case.json", case_spec.model_dump_json(indent=2))
    staged("gold/grading.md", grading_doc(task, sample))

    files.update(sample_logs(case_spec.attempts, stage=stage / "logs"))
    return files


def _sample_record(sample: Sample) -> dict[str, Any]:
    """A sample as a plain record, with Inspect's own field names."""
    record = sample.model_dump(exclude_none=True, exclude={"files", "sandbox", "setup"})
    return dict(record)


def case_sample(
    task: Task,
    sample: Sample,
    case_spec: CaseSpec,
    *,
    prompt: str,
    stage: Path,
    sandbox: SandboxEnvironmentType | None = None,
) -> Sample:
    """One audit case, as an Inspect `Sample`."""
    return Sample(
        id=str(case_spec.sample_id),
        input=prompt,
        target=sample.target,
        metadata={"case_spec": case_spec.model_dump()},
        sandbox=sandbox,
        files=materialise(task, sample, case_spec, stage=stage),
    )
