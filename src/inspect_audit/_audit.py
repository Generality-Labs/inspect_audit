import atexit
import os
import shutil
import tempfile
from collections.abc import Collection, Sequence
from pathlib import Path
from typing import Any

import pandas as pd
from inspect_ai import Task
from inspect_ai.agent import as_solver
from inspect_ai.analysis import EvalModel, SampleSummary, samples_df
from inspect_ai.dataset import MemoryDataset, Sample
from inspect_ai.log import EvalLog
from inspect_ai.solver import Generate, Solver, TaskState, solver
from inspect_ai.util._sandbox.environment import SandboxEnvironmentType

from ._agent import audit_agent, audit_items, item_scorer
from ._item import AUDIT_ROOT, AttemptRef, AuditItem, item_sample
from ._resolve import resolve_task
from ._sandbox import (
    audit_compose,
    audit_values,
    has_benchmark,
    run_benchmark_setup,
    sample_sandbox,
)

LogSource = str | list[str] | EvalLog | list[EvalLog]


@solver
def benchmark_setup() -> Solver:
    """Run the audited sample's own setup script in the benchmark service."""

    async def solve(state: TaskState, generate: Generate) -> TaskState:
        await run_benchmark_setup((state.metadata or {}).get("benchmark_setup"))
        return state

    return solve

ITEM_PROMPT = (
    f"You are auditing one benchmark sample. Its audit filesystem is at {AUDIT_ROOT}."
)


def attempts(logs: LogSource, *, sample_ids: Collection[str] | None = None) -> pd.DataFrame:
    """One row per recorded attempt, with a `score_*` column per scorer.

    Args:
        logs: Log directory, log files, or already-read `EvalLog`s.
        sample_ids: Restrict to these sample ids.
    """
    frame = samples_df(logs, columns=SampleSummary + EvalModel)
    if sample_ids is not None:
        wanted = {str(sample) for sample in sample_ids}
        frame = frame[frame["id"].astype(str).isin(wanted)]
    return frame.reset_index(drop=True)


def audit_task(
    task: str | Task,
    logs: LogSource | None = None,
    *,
    samples: Sequence[str | int] | None = None,
    limit: int | None = None,
    items: list[str] | None = None,
    task_args: dict[str, Any] | None = None,
    sandbox: SandboxEnvironmentType | None = None,
    solver: Solver | None = None,
    auditor_image: str | None = None,
    benchmark_image: str | None = None,
) -> Task:
    """Build the audit as an Inspect `Task`.

    Args:
        task: The task to audit -- registry name, `file.py@name`, or a `Task`.
        logs: Logs providing the recorded attempts at each sample.
        samples: Sample ids to audit (defaults to all, subject to `limit`).
        limit: Audit at most this many samples.
        items: Audit items to investigate (defaults to all of them).
        task_args: Task arguments used to resolve the audited task.
        sandbox: Override the sandbox (defaults to the audited task's own, else ours).
        solver: Override the auditor (defaults to `audit_agent()`).
        auditor_image: Emit the sandbox as Helm values for k8s providers, with this
            published image as the auditor (see `audit_values`).
        benchmark_image: Published image standing in for benchmark services that
            `build:` their own (k8s only).
    """
    target = resolve_task(task, task_args)
    staging = _staging()

    # one merged compose per distinct environment: ctf-style benchmarks give every
    # sample its own compose file, most give them all one
    composed: dict[str, SandboxEnvironmentType] = {}

    def environment(sample: Sample) -> SandboxEnvironmentType:
        if sandbox is not None:
            return sandbox
        spec = sample_sandbox(target, sample)
        key = str(spec.config) if spec is not None and isinstance(spec.config, str) else ""
        if key not in composed:
            stage = staging / "sandbox" / str(len(composed))
            if auditor_image is not None:
                composed[key] = audit_values(
                    target,
                    spec,
                    stage=stage,
                    auditor_image=auditor_image,
                    benchmark_image=benchmark_image,
                )
            else:
                composed[key] = audit_compose(target, spec, stage=stage)
        return composed[key]

    # select samples before reading attempts, so collection pushes down and auditing
    # five samples of a ten-thousand sample benchmark does not materialise every log
    dataset = list(target.dataset)
    ids = [
        str(sample.id) if sample.id is not None else str(index)
        for index, sample in enumerate(dataset, start=1)
    ]
    chosen = {str(s) for s in samples} if samples is not None else None
    selected = [sid for sid in ids if chosen is None or sid in chosen]
    if limit is not None:
        selected = selected[:limit]
    in_scope = set(selected)

    # group the attempts by sample
    by_sample: dict[str, list[AttemptRef]] = {}
    if logs is not None:
        frame = attempts(logs, sample_ids=in_scope)
        scores = [str(c) for c in frame.columns if str(c).startswith("score_")]
        for row in frame.to_dict("records"):
            sample_id = str(row["id"])
            by_sample.setdefault(sample_id, []).append(
                AttemptRef(
                    model=str(row["model"]),
                    epoch=int(row["epoch"]),
                    scores={name.removeprefix("score_"): str(row[name]) for name in scores},
                    log_file=str(row["log"]),
                    sample_id=sample_id,
                )
            )
        if not by_sample and in_scope:
            raise ValueError(
                f"No attempts at any selected sample of '{target.name}' were found in "
                "these logs. Check the task and its arguments match the logs."
            )

    # stage one audit sample per item
    audit_samples: list[Sample] = []
    for sample_id, sample in zip(ids, dataset, strict=True):
        if sample_id not in in_scope:
            continue
        item = AuditItem(
            task=target.name,
            task_args=task_args or {},
            sample_id=sample_id,
            attempts=by_sample.get(str(sample_id), []),
        )
        original_env = sample_sandbox(target, sample)
        audit_samples.append(
            item_sample(
                target,
                sample,
                item,
                prompt=ITEM_PROMPT,
                stage=staging / str(sample_id),
                sandbox=environment(sample),
                original_env=original_env,
                benchmark=sandbox is None and has_benchmark(original_env),
            )
        )

    return Task(
        name=f"audit/{target.name}",
        dataset=MemoryDataset(audit_samples),
        setup=benchmark_setup(),
        solver=solver
        or as_solver(audit_agent(items=items, benchmark_scorers=target.scorer)),
        scorer=[item_scorer(item.name) for item in audit_items(items)],
        metadata={"audited_task": target.name},
    )


def _staging() -> Path:
    # one directory per run, removed at exit; INSPECT_AUDIT_KEEP_STAGING=1 keeps it
    staging = Path(tempfile.mkdtemp(prefix="inspect_audit_"))
    if not os.environ.get("INSPECT_AUDIT_KEEP_STAGING"):
        atexit.register(shutil.rmtree, staging, ignore_errors=True)
    return staging
