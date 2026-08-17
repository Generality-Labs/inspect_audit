"""Pairing a task's samples with the attempts recorded against them.

Built from log headers and sample summaries only — never full samples — so it stays
cheap over thousands of logs.

Attempts are matched to samples on `(sample_id, epoch)`. Task names are deliberately
not used: the same benchmark is routinely logged under a different name (Epoch's
SimpleQA Verified logs are `simpleqa_verified`, two of them `bench/simpleqa_verified`,
while the published task resolves as `inspect_evals/simpleqa_verified`). A log that
shares no sample ids with the dataset is reported instead, which is what catches a
directory holding some other benchmark's logs.
"""

from collections.abc import Collection, Sequence
from pathlib import Path
from typing import Any

import pandas as pd
from inspect_ai import Task
from inspect_ai.dataset import Sample
from inspect_ai.log import (
    EvalLog,
    list_eval_logs,
    read_eval_log,
    read_eval_log_sample_summaries,
)
from inspect_ai.scorer import value_to_float

__all__ = ["attempts", "candidates", "is_pass"]

_to_float = value_to_float()

LogSource = str | Path | Sequence[str | Path] | Sequence[EvalLog]

_ATTEMPT_COLUMNS = [
    "sample_id",
    "epoch",
    "model",
    "log_file",
    "score",
    "answer",
    "error",
    "retries",
    "message_count",
]


def sample_id_of(sample: Sample, index: int) -> str:
    """The id Inspect will use for a sample: its own, else its 1-based position."""
    return str(sample.id if sample.id is not None else index)


def _log_files(logs: LogSource) -> list[str]:
    if isinstance(logs, (str, Path)):
        return [info.name for info in list_eval_logs(str(logs))]
    files: list[str] = []
    for entry in logs:
        if isinstance(entry, EvalLog):
            files.append(entry.location)
        elif Path(str(entry)).is_dir():
            files += [info.name for info in list_eval_logs(str(entry))]
        else:
            files.append(str(entry))
    return files


def attempts(
    task: Task,
    logs: LogSource,
    *,
    strict: bool = True,
    sample_ids: Collection[str] | None = None,
) -> pd.DataFrame:
    """One row per recorded attempt at a sample of `task`.

    Args:
        task: The resolved task being audited.
        logs: Log directory, log files, or already-read `EvalLog`s.
        strict: Raise if a log shares no sample ids with the task's dataset, which
            usually means the logs are for a different benchmark.
        sample_ids: Collect attempts for these samples only. The check for shared ids
            still runs over the whole dataset, so narrowing the selection cannot hide
            a log that does not belong.

    Returns:
        Columns: `sample_id`, `epoch`, `model`, `log_file`, `score`, `answer`,
        `error`, `retries`, `message_count`.
    """
    wanted = None if sample_ids is None else set(sample_ids)
    known = {sample_id_of(sample, index) for index, sample in enumerate(task.dataset, start=1)}

    rows: list[dict[str, Any]] = []
    unmatched: list[str] = []

    for file in _log_files(logs):
        header = read_eval_log(file, header_only=True)
        model = header.eval.model
        matched = 0

        for summary in read_eval_log_sample_summaries(file):
            sample_id = str(summary.id)
            matched += int(sample_id in known)
            if wanted is not None and sample_id not in wanted:
                continue

            score = next(iter((summary.scores or {}).values()), None)
            rows.append(
                {
                    "sample_id": sample_id,
                    "epoch": summary.epoch,
                    "model": model,
                    "log_file": file,
                    "score": None if score is None else score.value,
                    "answer": None if score is None else score.answer,
                    "error": summary.error,
                    "retries": summary.retries,
                    "message_count": summary.message_count,
                }
            )

        if matched == 0:
            unmatched.append(f"  {Path(file).name}: shares no sample ids with this dataset")

    if unmatched and strict:
        raise ValueError(
            f"{len(unmatched)} log(s) do not appear to be for `{task.name}`:\n"
            + "\n".join(unmatched)
            + "\nCheck the task and its arguments match the ones the logs were run with, "
            "or pass strict=False to include them anyway."
        )

    # Columns are named explicitly: a frame built from zero rows has no columns at all,
    # so every downstream column access raises KeyError instead of returning an empty
    # result. Selecting nothing is a legitimate outcome.
    return pd.DataFrame(rows, columns=_ATTEMPT_COLUMNS)


def candidates(
    task: Task,
    logs: LogSource,
    *,
    strict: bool = True,
) -> pd.DataFrame:
    """One row per sample of `task`, with the field's attempts summarised onto it.

    This is the frame you filter to choose what to audit — `cand[cand.never_solved]`,
    `cand[cand.pass_rate < 0.1]` — so it is sample-level, not attempt-level.

    Returns:
        Columns: `sample_id`, `input`, `target`, `metadata`, `n_attempts`,
        `n_models`, `models`, `n_correct`, `pass_rate`, `never_solved`,
        `all_solved`, `log_files`.
    """
    frame = attempts(task, logs, strict=strict)
    correct = (
        frame["score"].apply(is_pass)
        if len(frame)
        else pd.Series(dtype=bool)
    )
    frame = frame.assign(correct=correct)

    grouped = (
        frame.groupby("sample_id")
        .agg(
            n_attempts=("model", "size"),
            n_models=("model", "nunique"),
            models=("model", lambda s: sorted(set(s))),
            n_correct=("correct", "sum"),
            pass_rate=("correct", "mean"),
            log_files=("log_file", lambda s: sorted(set(s))),
        )
        .reset_index()
    )
    grouped["never_solved"] = grouped["n_correct"] == 0
    grouped["all_solved"] = grouped["n_correct"] == grouped["n_attempts"]

    dataset = pd.DataFrame(
        {
            "sample_id": sample_id_of(sample, index),
            "input": sample.input if isinstance(sample.input, str) else str(sample.input),
            "target": sample.target,
            "metadata": [sample.metadata or {}],
        }
        for index, sample in enumerate(task.dataset, start=1)
    )

    merged = dataset.merge(grouped, on="sample_id", how="left")
    merged["n_attempts"] = merged["n_attempts"].fillna(0).astype(int)
    return merged


def is_pass(value: Any) -> bool:
    """Whether a score value counts as a pass.

    Uses Inspect's own `value_to_float`, which is the interpretation its metrics use:
    `C`/`I`/`P`/`N`, common boolean spellings, and numeric strings all map the way the
    rest of the ecosystem maps them.

    Args:
        value: The score value, in whatever shape the scorer produced.
    """
    return _to_float(value) >= 1.0


def log_headers(logs: LogSource) -> list[EvalLog]:
    """Header-only reads of every log in `logs`.

    Headers carry the agent's configuration and the grading options that produced
    these attempts, which is what makes elicitation recoverable. `header_only` keeps
    it to metadata, so this stays cheap across thousands of logs.
    """
    return [read_eval_log(file, header_only=True) for file in _log_files(logs)]
