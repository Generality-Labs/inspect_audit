"""The audit case: what one sample's audit filesystem contains, and how it is built.

A case is one benchmark sample. Its filesystem is materialised into the sample's
sandbox by Inspect (via `Sample.files`), so nothing is stored between runs — the
`CaseSpec` in `Sample.metadata` is enough to rebuild an identical case from the audit
log alone.

Only bounded content is materialised. Anything unbounded (whole transcripts,
repositories) is reached through tools or the task's own sandbox instead.
"""

import json
from typing import Any

from inspect_ai import Task
from inspect_ai.dataset import Sample
from inspect_ai.log import EvalLog
from inspect_ai.util._sandbox.environment import SandboxEnvironmentType
from pydantic import BaseModel, Field

from ._elicitation import agent_configs, judge_criteria, pass_values
from ._gold import gold_facts, grading_doc, render_input

__all__ = ["AUDIT_ROOT", "AttemptRef", "CaseSpec", "case_sample", "materialise"]

AUDIT_ROOT = "/audit"


class AttemptRef(BaseModel):
    """One recorded attempt at a sample: how a model answered, and where to look.

    The graded answer is carried inline because it comes free from a log's sample
    summaries, and the distribution of answers across the field is the single most
    useful thing an auditor can see. The log pointer is kept so a full transcript
    can be fetched on demand rather than copied.
    """

    model: str
    epoch: int
    score: str | float | bool | None = None
    answer: str | None = None
    log_file: str
    sample_id: str | int


class CaseSpec(BaseModel):
    """Everything needed to rebuild a case, carried in `Sample.metadata`.

    This is the audit's provenance record: which task, resolved with which
    arguments, which sample, which attempts were in scope, and a hash of the
    sample input so a later reader can tell whether the pairing still holds.
    """

    task: str
    task_args: dict[str, Any] = Field(default_factory=dict)
    sample_id: str | int
    input_hash: str
    attempts: list[AttemptRef] = Field(default_factory=list)
    items: list[str] = Field(default_factory=list)


def materialise(
    task: Task,
    sample: Sample,
    case_spec: CaseSpec,
    field: dict[str, Any] | None = None,
    extra: dict[str, str] | None = None,
    logs: list[EvalLog] | None = None,
) -> dict[str, str]:
    """Build the `Sample.files` mapping for a case.

    Keys are absolute paths inside the sandbox; values are file contents.
    """
    input_text = render_input(sample.input)
    sample_json: dict[str, Any] = {
        "task": task.name,
        "sample_id": case_spec.sample_id,
        "input": input_text,
        "target": sample.target,
        "metadata": sample.metadata or {},
    }
    files: dict[str, str] = {
        f"{AUDIT_ROOT}/sample.json": json.dumps(sample_json, indent=2, ensure_ascii=False),
        f"{AUDIT_ROOT}/prompt.txt": input_text,
        f"{AUDIT_ROOT}/case.json": case_spec.model_dump_json(indent=2),
    }
    criteria = judge_criteria(task, logs or [])
    files[f"{AUDIT_ROOT}/elicitation.json"] = json.dumps(
        {
            "agent": agent_configs(logs or []),
            "judge": criteria,
            "pass_values": sorted(pass_values(criteria) or []),
        },
        indent=2,
        default=str,
    )
    facts = gold_facts(sample)
    files[f"{AUDIT_ROOT}/gold/target.txt"] = (
        facts["target"] if isinstance(facts["target"], str) else "\n".join(facts["target"])
    )
    files[f"{AUDIT_ROOT}/gold/grading.md"] = grading_doc(task, sample, criteria)
    if facts["cited_urls"]:
        files[f"{AUDIT_ROOT}/gold/sources.md"] = "# Sources cited by the benchmark\n\n" + "\n".join(
            f"- {u}" for u in facts["cited_urls"]
        ) + "\n"

    if case_spec.attempts:
        # One line per recorded attempt: how the field actually answered. Summaries
        # carry the graded answer, so this costs nothing beyond reading them.
        files[f"{AUDIT_ROOT}/attempts/index.jsonl"] = (
            "\n".join(a.model_dump_json() for a in case_spec.attempts) + "\n"
        )
    if field:
        files[f"{AUDIT_ROOT}/attempts/field.json"] = json.dumps(field, indent=2, default=str)

    if extra:
        # Values here are host paths rather than contents, so large trajectories are
        # copied into the sandbox without travelling through the sample (and so
        # without being written into the audit log).
        files.update(extra)

    return files


def case_sample(
    task: Task,
    sample: Sample,
    case_spec: CaseSpec,
    *,
    prompt: str,
    sandbox: SandboxEnvironmentType | None = None,
    field: dict[str, Any] | None = None,
    extra: dict[str, str] | None = None,
    logs: list[EvalLog] | None = None,
) -> Sample:
    """One audit case, as an Inspect `Sample`."""
    return Sample(
        id=str(case_spec.sample_id),
        input=prompt,
        target=sample.target,
        metadata={"case_spec": case_spec.model_dump()},
        sandbox=sandbox,
        files=materialise(task, sample, case_spec, field, extra, logs),
    )
