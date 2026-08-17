"""Assembling the audit itself.

An audit is an Inspect `Task`: its dataset is audit cases, its solver is an
auditing agent, its scorer validates the verdict. Everything about running it —
sandbox lifecycle, concurrency, retries, resume, logging, the viewer — is
inherited rather than built.

At v0 the solver only probes the sandbox, so the audit proves the filesystem was
materialised correctly. The auditing agent replaces it unchanged.
"""

import tempfile
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pandas as pd
from inspect_ai import Task
from inspect_ai.dataset import MemoryDataset, Sample
from inspect_ai.solver import Generate, Solver, TaskState, solver
from inspect_ai.util import sandbox as sandbox_env
from inspect_ai.util._sandbox.environment import SandboxEnvironmentType

from ._candidates import LogSource, input_hash, is_pass, log_headers, sample_id_of
from ._candidates import attempts as attempt_rows
from ._case import AUDIT_ROOT, AttemptRef, CaseSpec, case_sample
from ._elicitation import judge_criteria, pass_values
from ._resolve import resolve_task
from ._sandbox import audit_sandbox
from ._trajectories import trajectory_files

__all__ = ["audit_task"]

CASE_PROMPT = (
    f"You are auditing one benchmark sample. Its audit filesystem is at {AUDIT_ROOT}."
)

# A task that declares no sandbox of its own still gets one: every audit runs in a
# sandbox, one code path for every benchmark. Ours is generated per task so the
# task's own packages are installed and its grading code is readable in place.


@solver
def probe_sandbox() -> Solver:
    """v0 solver: read the case filesystem back out of the sandbox.

    Exists so the audit is self-verifying — the log records what was actually
    present in each container, rather than us inspecting it by hand. The auditing
    agent replaces it.

    Must be `@solver`-registered: Inspect writes the solver chain into the log's plan
    and fails the run with "does not have registry info" for a bare callable. An
    undecorated solver assembles fine and only breaks when an eval actually runs,
    which is why `tests/test_run.py` runs one.
    """

    async def solve(state: TaskState, generate: Generate) -> TaskState:
        listing = await sandbox_env().exec(["find", AUDIT_ROOT, "-type", "f"])
        case_spec = await sandbox_env().read_file(f"{AUDIT_ROOT}/case.json")
        state.store.set("audit_files", sorted(listing.stdout.split()))
        state.store.set("case_readback", case_spec[:400])
        return state

    return solve


def audit_task(
    task: str | Task,
    logs: LogSource | None = None,
    *,
    samples: Sequence[str | int] | None = None,
    limit: int | None = None,
    task_args: dict[str, Any] | None = None,
    sandbox: SandboxEnvironmentType | None = None,
    strict: bool = True,
    trajectories: bool = True,
) -> Task:
    """Build the audit as an Inspect `Task`.

    Args:
        task: The task to audit — registry name, `file.py@name`, or a `Task`.
        logs: Logs providing the recorded attempts at each sample. Without them an
            auditor can only judge a sample in isolation.
        samples: Sample ids to audit (defaults to all, subject to `limit`).
        limit: Audit at most this many samples.
        task_args: Task arguments used to resolve the audited task.
        sandbox: Override the sandbox (defaults to the audited task's own, else ours).
        strict: Raise if logs cannot be verified against the task's dataset.
        trajectories: Materialise each attempt's full trajectory (one file per model
            per epoch). Outputs alone hide how a model failed, so this defaults on.

    Returns:
        A task to run with `eval()` / `eval_set()`.
    """
    target = resolve_task(task, task_args)
    box = sandbox or target.sandbox or audit_sandbox(target)

    # Read headers once: they carry the agent's configuration and what actually graded
    # these attempts. Cheap (header_only), and required for elicitation to mean anything.
    headers = log_headers(logs) if logs is not None else []
    passing = pass_values(judge_criteria(target, headers))

    by_sample: dict[str, list[AttemptRef]] = {}
    if logs is not None:
        for row in attempt_rows(target, logs, strict=strict).to_dict("records"):
            by_sample.setdefault(str(row["sample_id"]), []).append(
                AttemptRef(
                    model=str(row["model"]),
                    epoch=int(row["epoch"]),
                    score=_opt(row["score"]),
                    answer=_opt(row["answer"]),
                    log_file=str(row["log_file"]),
                    sample_id=str(row["sample_id"]),
                )
            )

    # One staging directory for the whole run, not one per case: a thousand cases
    # would otherwise leave a thousand temporary directories behind.
    staging = Path(tempfile.mkdtemp(prefix="inspect_audit_"))

    wanted = {str(s) for s in samples} if samples else None
    cases: list[Sample] = []
    for index, sample in enumerate(target.dataset, start=1):
        sample_id = sample_id_of(sample, index)
        if wanted is not None and str(sample_id) not in wanted:
            continue
        refs = by_sample.get(str(sample_id), [])
        case_spec = CaseSpec(
            task=target.name,
            task_args=task_args or {},
            sample_id=sample_id,
            input_hash=input_hash(sample.input),
            attempts=refs,
        )
        cases.append(
            case_sample(
                target,
                sample,
                case_spec,
                prompt=CASE_PROMPT,
                sandbox=box,
                field=_field_summary(refs, passing),
                extra=trajectory_files(refs, stage=staging / str(sample_id))
                if trajectories
                else None,
                logs=headers,
            )
        )
        if limit is not None and len(cases) >= limit:
            break

    return Task(
        name=f"audit/{target.name}",
        dataset=MemoryDataset(cases),
        solver=probe_sandbox(),
        metadata={"audited_task": target.name},
    )


def _field_summary(
    refs: list[AttemptRef], passing: set[str] | None = None
) -> dict[str, Any] | None:
    """How the field as a whole did on this sample, for the auditor's context."""
    if not refs:
        return None
    passed = [r for r in refs if is_pass(r.score, passing)]
    return {
        "n_attempts": len(refs),
        "n_models": len({r.model for r in refs}),
        "n_correct": len(passed),
        "pass_rate": round(len(passed) / len(refs), 4),
        "never_solved": len(passed) == 0,
        "distinct_answers": len({(r.answer or "").strip().lower() for r in refs}),
    }


def _opt(value: Any) -> Any:
    """Pandas turns missing values into NaN; models want None.

    Reading a frame back row-wise reintroduces float NaN wherever a column held
    nulls, which then fails validation against an optional string field.
    """
    return None if value is None or (isinstance(value, float) and pd.isna(value)) else value
