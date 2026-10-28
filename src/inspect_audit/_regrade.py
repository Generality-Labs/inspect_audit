"""Does the installed grader reproduce the recorded grades? Inspect's own rescoring, diffed.

Each declared log is re-scored with the benchmark's scorers through Inspect's public
`score_async` (append, on a copy), and every fresh grade is set beside the recorded
one. A disagreement is re-scored again: varying regrades are judge noise; identical
ones are a stable disagreement -- the grader as installed is not the grader that
produced the log. Runs outside any investigation, on the host, with no sandbox: a scorer
that needs the benchmark's box is reported, not worked around.
"""

import asyncio
import csv
import math
from copy import deepcopy
from dataclasses import asdict, dataclass
from functools import partial
from pathlib import Path
from typing import Any

import anyio.to_thread
from inspect_ai import Task, score_async
from inspect_ai.dataset import Sample
from inspect_ai.log import EvalLog, read_eval_log, read_eval_log_sample
from inspect_ai.model import Model
from inspect_ai.scorer import CORRECT, INCORRECT, NOANSWER, PARTIAL, Scorer, Value
from inspect_ai.scorer._scorer import unique_scorer_name
from inspect_ai.solver import Generate, Solver, TaskState, solver
from inspect_ai.util import registry_create, registry_info

from ._logs import index_logs
from ._task import configured_judges, judge_roles

AGREE = "AGREE"
STABLE = "STABLE_DISAGREEMENT"
NOISY = "NOISY_DISAGREEMENT"
NEEDS_BOX = "NEEDS_BOX"
ERROR = "ERROR"
UNSCORED = "UNSCORED"
# inspect's own categorical values compare the way its metrics read them; any other
# string is a label, and two different labels are two different grades
_STANDARD = {CORRECT: 1.0, INCORRECT: 0.0, PARTIAL: 0.5, NOANSWER: 0.0}


def comparable(value: Value | None) -> Any:
    """A grade in comparable form: 1, 1.0, True and "C" are one grade; "A" and "B" two."""
    if value is None:
        return UNSCORED
    if isinstance(value, bool):
        return float(value)
    if isinstance(value, (int, float)):
        return UNSCORED if math.isnan(value) else float(value)
    if isinstance(value, str):
        return _STANDARD.get(value, value)
    if isinstance(value, dict):
        return {str(k): comparable(v) for k, v in sorted(value.items())}
    return [comparable(v) for v in value]


@dataclass
class Regrade:
    """One recorded grade beside its regrade."""

    log: str
    eval_id: str
    sample_id: str
    epoch: int
    scorer: str
    recorded: str
    regraded: str
    status: str
    detail: str = ""


def _name(scorer: Scorer) -> str:
    return registry_info(scorer).name.split("/")[-1]


async def _rescore(log: EvalLog, scorers: list[Scorer], roles: dict[str, str | Model]) -> EvalLog:
    # reducer named explicitly: the log's own may not resolve here, and the investigation
    # compares per-sample grades, never aggregates
    return await score_async(
        log,
        scorers,
        action="append",
        epochs_reducer="mode",
        model_roles=dict(roles) or None,
        display="none",
        copy=True,
    )


def _fresh(
    original: dict[str, Any], rescored: dict[str, Any], names: list[str]
) -> list[tuple[str, str | None]]:
    """Each scorer beside the score name the rescore gave it, None where it gave none.

    Mirrors Inspect's append naming (`score._score_sample`): a scorer's name is made
    unique against the sample's scores so far, and a scorer returning None adds
    nothing. Pairing by position misfiled every later score after one None (FRICTION 4).
    """
    used = set(original)
    pairs: list[tuple[str, str | None]] = []
    for name in names:
        candidate = unique_scorer_name(name, list(used))
        if candidate in rescored:
            used.add(candidate)
            pairs.append((name, candidate))
        else:
            pairs.append((name, None))
    return pairs


async def regrade_async(
    task: str | Task,
    *,
    logs: list[str],
    task_args: dict[str, Any] | None = None,
    judge: dict[str, str] | None = None,
    resamples: int = 3,
) -> list[Regrade]:
    """Regrade every recorded attempt in the declared logs with the installed scorers."""
    if isinstance(task, Task):
        their, name, package = task, task.name, None
    else:
        their = registry_create("task", task, **(task_args or {}))
        name = registry_info(their).name
        package = name.split("/")[0] if "/" in name else None
    index = index_logs(logs, task_name=name, task_args=dict(task_args or {}), package=package)
    roles = configured_judges(index, judge_roles(index, judge))
    scorers: list[Scorer] = (
        list(their.scorer) if isinstance(their.scorer, list) else [s for s in [their.scorer] if s]
    )
    if not scorers:
        raise ValueError(f"task {name!r} declares no scorer to regrade with")
    names = [_name(s) for s in scorers]

    rows: list[Regrade] = []
    for path in logs:
        log = read_eval_log(path)
        samples = log.samples or []
        try:
            rescored = await _rescore(log, scorers, roles)
        except Exception as ex:
            status = NEEDS_BOX if "sandbox" in str(ex).lower() else ERROR
            rows += [
                Regrade(
                    path,
                    log.eval.eval_id,
                    str(s.id),
                    s.epoch,
                    "*",
                    "",
                    "",
                    status,
                    f"{type(ex).__name__}: {ex}",
                )
                for s in samples
            ]
            continue
        for before, after in zip(samples, rescored.samples or [], strict=True):
            recorded = dict(before.scores or {})
            for scorer_name, fresh_name in _fresh(recorded, dict(after.scores or {}), names):
                old = recorded.get(scorer_name)
                new = (after.scores or {})[fresh_name].value if fresh_name else None
                rows.append(
                    Regrade(
                        path,
                        log.eval.eval_id,
                        str(before.id),
                        before.epoch,
                        scorer_name,
                        str(old.value) if old is not None else UNSCORED,
                        str(new) if new is not None else UNSCORED,
                        AGREE
                        if old is not None
                        and new is not None
                        and comparable(old.value) == comparable(new)
                        else STABLE,
                    )
                )

        # a disagreement re-scored: any variation is noise, none is a stable fault
        disputed = [r for r in rows if r.log == path and r.status == STABLE]
        if disputed and resamples:
            keys = {(r.sample_id, r.epoch) for r in disputed}
            subset = log.model_copy(
                update={"samples": [s for s in samples if (str(s.id), s.epoch) in keys]}
            )
            for _ in range(resamples):
                again = await _rescore(subset, scorers, roles)
                for before, after in zip(subset.samples or [], again.samples or [], strict=True):
                    pairs = _fresh(dict(before.scores or {}), dict(after.scores or {}), names)
                    for scorer_name, fresh_name in pairs:
                        value = (after.scores or {})[fresh_name].value if fresh_name else None
                        for row in disputed:
                            same = (row.sample_id, row.epoch, row.scorer) == (
                                str(before.id),
                                before.epoch,
                                scorer_name,
                            )
                            if same and comparable(
                                str(value) if value is not None else UNSCORED
                            ) != comparable(row.regraded):
                                row.status = NOISY
    return rows


def regrade(
    task: str | Task,
    *,
    logs: list[str],
    task_args: dict[str, Any] | None = None,
    judge: dict[str, str] | None = None,
    resamples: int = 3,
) -> list[Regrade]:
    """Synchronous `regrade_async`."""
    return asyncio.run(
        regrade_async(task, logs=logs, task_args=task_args, judge=judge, resamples=resamples)
    )


def write_csv(rows: list[Regrade], path: str | Path) -> None:
    """One row per (attempt, scorer): recorded grade, regrade, status."""
    with open(path, "w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(Regrade.__dataclass_fields__))
        writer.writeheader()
        writer.writerows(asdict(row) for row in rows)


def main(argv: list[str] | None = None) -> int:
    """`inspect-audit-regrade --task T --logs a.eval b.eval --out regrade.csv`."""
    import argparse
    import json
    from collections import Counter

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument("--task", required=True, help="registry name, e.g. inspect_evals/simpleqa")
    parser.add_argument("--logs", nargs="+", required=True, help=".eval files of that task")
    parser.add_argument("--task-args", default="{}", help="the task's arguments as JSON")
    parser.add_argument("--judge", default=None, help="model roles as JSON, if the logs lack them")
    parser.add_argument("--resamples", type=int, default=3)
    parser.add_argument("--out", required=True, help="CSV to write")
    args = parser.parse_args(argv)
    rows = regrade(
        args.task,
        logs=args.logs,
        task_args=json.loads(args.task_args),
        judge=json.loads(args.judge) if args.judge else None,
        resamples=args.resamples,
    )
    write_csv(rows, args.out)
    for status, count in sorted(Counter(r.status for r in rows).items()):
        print(f"{status}: {count}")
    print(f"wrote {len(rows)} rows to {args.out}")
    return 1 if any(r.status in (STABLE, ERROR, NEEDS_BOX) for r in rows) else 0


# where a replayed sample keeps what it replays, beside the benchmark's own metadata
REPLAY = "inspect_audit_regrade"


@solver
def replay() -> Solver:
    """Put the recorded attempt back: its transcript, final output and solver store."""

    async def solve(state: TaskState, generate: Generate) -> TaskState:
        recorded = state.metadata[REPLAY]
        # off the event loop: Inspect's sync readers re-enter a running one (FRICTION 44)
        sample = await anyio.to_thread.run_sync(
            partial(
                read_eval_log_sample,
                recorded["log"],
                id=recorded["sample_id"],
                epoch=recorded["epoch"],
                resolve_attachments=True,
            )
        )
        state.messages = deepcopy(sample.messages)
        state.output = sample.output
        for key, value in (sample.store or {}).items():
            state.store.set(key, deepcopy(value))
        return state

    return solve


def regrade_task(
    task: str | Task,
    *,
    logs: list[str],
    task_args: dict[str, Any] | None = None,
    judge: dict[str, str] | None = None,
    samples: list[str] | None = None,
    epochs: int = 3,
    cost_limit: float | None = None,
) -> Task:
    """Re-grade recorded attempts with the benchmark's own scorers, as an Inspect task.

    One sample per recorded attempt (optionally only `samples`), replayed without
    calling a model, scored `epochs` times: epochs that disagree are judge noise, and
    epochs that agree with each other but not with the recorded grade are a stable
    disagreement. Run on Hawk with the judge as the eval's model, the job gets the
    judge's credentials, which the auditor's own box does not have (FRICTION 20).
    Each sample's metadata keeps the recorded grades under `inspect_audit_regrade`.
    """
    if isinstance(task, Task):
        their, name, package = task, task.name, None
    else:
        their = registry_create("task", task, **(task_args or {}))
        name = registry_info(their).name
        package = name.split("/")[0] if "/" in name else None
    index = index_logs(logs, task_name=name, task_args=dict(task_args or {}), package=package)
    roles = configured_judges(index, judge_roles(index, judge))
    scorers: list[Scorer] = (
        list(their.scorer) if isinstance(their.scorer, list) else [s for s in [their.scorer] if s]
    )
    if not scorers:
        raise ValueError(f"task {name!r} declares no scorer to regrade with")
    wanted = set(samples) if samples is not None else None
    dataset: list[Sample] = []
    for sample_id, attempts in sorted(index.attempts.items()):
        if wanted is not None and sample_id not in wanted:
            continue
        for attempt in attempts:
            # at task construction, outside any event loop, so the sync reader is safe
            recorded = read_eval_log_sample(
                attempt.log, id=attempt.sample_id, epoch=attempt.epoch, resolve_attachments=True
            )
            dataset.append(
                Sample(
                    id=f"{attempt.eval_id}/{attempt.sample_id}/{attempt.epoch}",
                    input=recorded.input,
                    target=recorded.target,
                    choices=recorded.choices,
                    metadata={
                        **(recorded.metadata or {}),
                        REPLAY: {
                            "log": attempt.log,
                            "eval_id": attempt.eval_id,
                            "sample_id": attempt.sample_id,
                            "epoch": attempt.epoch,
                            "model": attempt.model,
                            "recorded": {k: v.value for k, v in (recorded.scores or {}).items()},
                        },
                    },
                )
            )
    if wanted is not None and not dataset:
        raise ValueError("none of the selected samples has a recorded attempt in these logs")
    return Task(
        dataset=dataset,
        solver=replay(),
        scorer=scorers,
        epochs=epochs,
        model_roles=roles or None,
        cost_limit=cost_limit,
        name=f"regrade/{name}",
        metadata={"regraded_task": name, "regraded_logs": list(logs)},
    )
