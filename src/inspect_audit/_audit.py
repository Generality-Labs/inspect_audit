"""Assembling the audit itself.

An audit is an Inspect `Task`: its dataset is audit cases, its solver is an
auditing agent, its scorer validates the verdict. Everything about running it —
sandbox lifecycle, concurrency, retries, resume, logging, the viewer — is
inherited rather than built.

At v0 the solver only probes the sandbox, so the audit proves the filesystem was
materialised correctly. The auditing agent replaces it unchanged.
"""

import atexit
import json
import os
import shutil
import tempfile
from collections.abc import Sequence
from pathlib import Path
from textwrap import dedent
from typing import Any

from inspect_ai import Task
from inspect_ai.agent import as_solver
from inspect_ai.dataset import MemoryDataset, Sample
from inspect_ai.solver import Generate, Solver, TaskState, solver
from inspect_ai.util import sandbox as sandbox_env
from inspect_ai.util._sandbox.environment import SandboxEnvironmentType

from ._agent import audit_agent, verdict
from ._candidates import LogSource, sample_id_of, score_columns
from ._candidates import attempts as attempt_rows
from ._compose import audit_compose
from ._item import AUDIT_ROOT, AttemptRef, AuditItem, item_sample
from ._resolve import resolve_task

__all__ = ["audit_task"]

CASE_PROMPT = (
    f"You are auditing one benchmark sample. Its audit filesystem is at {AUDIT_ROOT}."
)

# A task that declares no sandbox of its own still gets one: every audit runs in a
# sandbox, one code path for every benchmark. Ours is generated per task so the
# task's own packages are installed and its grading code is readable in place.

# Run inside the sandbox with the audited task's own `inspect_ai`, to prove the case
# logs are genuine logs rather than files that merely arrived.
_READ_LOGS = dedent(f"""
    import glob, json
    from inspect_ai.log import read_eval_log

    out = []
    for file in sorted(glob.glob("{AUDIT_ROOT}/logs/*.eval")):
        log = read_eval_log(file)
        out.append(
            dict(
                file=file,
                task=log.eval.task,
                model=log.eval.model,
                samples=len(log.samples or []),
                scorers=[s.name for s in (log.eval.scorers or [])],
            )
        )
    print(json.dumps(out))
""")


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
        listing = await sandbox_env().exec(
            ["find", AUDIT_ROOT, "-type", "f", "-print"], timeout=60
        )
        if not listing.success:
            raise RuntimeError(f"could not list {AUDIT_ROOT}: {listing.stderr}")
        state.store.set("audit_files", sorted(listing.stdout.splitlines()))

        # The claim worth checking is not that files exist, but that they are the real
        # artefacts: that the task's own installed `inspect_ai` opens the sliced logs.
        # This is exactly what the auditing agent will do, so failing here fails early.
        probe = await sandbox_env().exec(["python", "-c", _READ_LOGS], timeout=300)
        if not probe.success:
            raise RuntimeError(f"could not read the case logs in the sandbox: {probe.stderr}")
        state.store.set("logs_readable", json.loads(probe.stdout))
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
    solver: Solver | None = None,
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
        solver: Override the auditor (defaults to `audit_agent()`). Pass
            `probe_sandbox()` to check the item filesystem without spending on a model.

    Returns:
        A task to run with `eval()` / `eval_set()`.
    """
    target = resolve_task(task, task_args)
    staging = _staging()
    # The audited task's own environment plus the auditor's, side by side.
    box = sandbox or audit_compose(target, stage=staging / "sandbox")

    # Decide what to audit before reading the attempts, so collection can be pushed
    # down to the selected samples. Auditing five samples of a ten-thousand sample
    # benchmark otherwise materialises every attempt of every log, answers included.
    dataset = list(target.dataset)
    ids = [sample_id_of(sample, index) for index, sample in enumerate(dataset, start=1)]
    # `samples is not None` rather than a truth test: an empty selection means audit
    # nothing, which is what a filter that matched nothing should produce.
    chosen = {str(s) for s in samples} if samples is not None else None
    selected = [sid for sid in ids if chosen is None or sid in chosen]
    if limit is not None:
        selected = selected[:limit]
    in_scope = set(selected)

    by_sample: dict[str, list[AttemptRef]] = {}
    if logs is not None:
        frame = attempt_rows(logs, sample_ids=in_scope)
        scores = score_columns(frame)
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
                f"no attempts at any selected sample of `{target.name}` were found in these "
                "logs. Check the task and its arguments match the ones the logs were run with."
            )

    items: list[Sample] = []
    for sample_id, sample in zip(ids, dataset, strict=True):
        if sample_id not in in_scope:
            continue
        item = AuditItem(
            task=target.name,
            task_args=task_args or {},
            sample_id=sample_id,
            attempts=by_sample.get(str(sample_id), []),
        )
        items.append(
            item_sample(
                target,
                sample,
                item,
                prompt=CASE_PROMPT,
                stage=staging / str(sample_id),
                sandbox=box,
            )
        )

    return Task(
        name=f"audit/{target.name}",
        dataset=MemoryDataset(items),
        solver=solver or as_solver(audit_agent()),
        scorer=verdict(),
        metadata={"audited_task": target.name},
    )


def _staging() -> Path:
    """A directory for one run's item files, removed when the process exits.

    One directory per run rather than per item, and cleaned up on the way out: the
    sliced logs are the bulk of an item's filesystem, so auditing a few hundred items
    stages a couple of gigabytes. Set `INSPECT_AUDIT_KEEP_STAGING=1` to keep it, which
    is how you inspect what an auditor was actually given.
    """
    staging = Path(tempfile.mkdtemp(prefix="inspect_audit_"))
    if not os.environ.get("INSPECT_AUDIT_KEEP_STAGING"):
        atexit.register(shutil.rmtree, staging, ignore_errors=True)
    return staging
