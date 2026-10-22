"""Does the installed grader reproduce the recorded grades? Inspect's own rescoring, diffed.

Each declared log is re-scored with the benchmark's scorers through Inspect's public
`score_async` (append, on a copy), and every fresh grade is set beside the recorded
one. A disagreement is re-scored again: varying regrades are judge noise; identical
ones are a stable disagreement -- the grader as installed is not the grader that
produced the log. Runs outside any audit, on the host, with no sandbox: a scorer
that needs the benchmark's box is reported, not worked around.
"""

import asyncio
import csv
import math
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from inspect_ai import Task, score_async
from inspect_ai.log import EvalLog, read_eval_log
from inspect_ai.scorer import CORRECT, INCORRECT, NOANSWER, PARTIAL, Scorer, Value
from inspect_ai.util import registry_create, registry_info

from ._logs import index_logs
from ._task import judge_roles

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


async def _rescore(log: EvalLog, scorers: list[Scorer], roles: dict[str, str]) -> EvalLog:
    # reducer named explicitly: the log's own may not resolve here, and the audit
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


def _fresh(original: dict[str, Any], rescored: dict[str, Any]) -> list[str]:
    """Score names the rescore added, in the order it added them."""
    return [name for name in rescored if name not in original]


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
    roles = judge_roles(index, judge)
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
            fresh = _fresh(recorded, dict(after.scores or {}))
            for scorer_name, fresh_name in zip(names, fresh, strict=False):
                old = recorded.get(scorer_name)
                new = (after.scores or {})[fresh_name]
                rows.append(
                    Regrade(
                        path,
                        log.eval.eval_id,
                        str(before.id),
                        before.epoch,
                        scorer_name,
                        str(old.value) if old is not None else UNSCORED,
                        str(new.value),
                        AGREE
                        if old is not None and comparable(old.value) == comparable(new.value)
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
                    fresh = _fresh(dict(before.scores or {}), dict(after.scores or {}))
                    for scorer_name, fresh_name in zip(names, fresh, strict=False):
                        value = (after.scores or {})[fresh_name].value
                        for row in disputed:
                            same = (row.sample_id, row.epoch, row.scorer) == (
                                str(before.id),
                                before.epoch,
                                scorer_name,
                            )
                            if same and comparable(str(value)) != comparable(row.regraded):
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
