"""Pairing a task's samples with the attempts recorded against them.

Built from log headers and sample summaries only — never full samples — so it stays
cheap over thousands of logs. Summaries carry the sample `input`, `target` and each
score's `answer`, which is everything needed both to verify the pairing and to show
an auditor how the field answered.

Pairings are matched on `(sample_id, epoch)` and then **verified by hashing the
sample input**. Names are not trusted: the same benchmark is routinely run under a
different task name (Epoch's SimpleQA Verified logs are `simpleqa_verified`, while
the published task resolves as `inspect_evals/simpleqa_verified`), and a task whose
dataset changed would otherwise pair the same id to a different question.
"""

import hashlib
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

__all__ = ["attempts", "candidates", "input_hash", "is_pass"]

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
    "verified",
]


def input_hash(value: Any) -> str:
    """Stable short hash of a sample input, used to verify a pairing."""
    text = value if isinstance(value, str) else str(value)
    return hashlib.sha256(text.encode()).hexdigest()[:16]


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
        strict: Raise if a log's samples cannot be verified against the task's
            dataset. With `strict=False` those rows are dropped and reported in
            the `verified` column instead.
        sample_ids: Collect attempts for these samples only. Verification still
            runs over the whole dataset, so restricting the selection cannot turn a
            mispaired log into an accepted one.

    Returns:
        Columns: `sample_id`, `epoch`, `model`, `log_file`, `score`, `answer`,
        `error`, `retries`, `message_count`, `verified`.
    """
    wanted = None if sample_ids is None else set(sample_ids)
    expected = {
        sample_id_of(sample, index): input_hash(sample.input)
        for index, sample in enumerate(task.dataset, start=1)
    }

    rows: list[dict[str, Any]] = []
    unverified: list[str] = []

    for file in _log_files(logs):
        header = read_eval_log(file, header_only=True)
        model = header.eval.model
        matched = 0

        for summary in read_eval_log_sample_summaries(file):
            sample_id = str(summary.id)
            want = expected.get(sample_id)
            verified = want is not None and want == input_hash(summary.input)
            matched += int(verified)
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
                    "verified": verified,
                }
            )

        if matched == 0:
            unverified.append(f"  {Path(file).name}: no samples matched this task's dataset")

    if unverified and strict:
        raise ValueError(
            f"{len(unverified)} log(s) could not be paired with `{task.name}`:\n"
            + "\n".join(unverified)
            + "\nCheck the task and its arguments match the ones the logs were run with, "
            "or pass strict=False to drop them."
        )

    # Columns are named explicitly: a frame built from zero rows has no columns at
    # all, so every downstream `frame["verified"]` becomes a KeyError instead of an
    # empty result. Selecting nothing is a legitimate outcome.
    frame = pd.DataFrame(rows, columns=_ATTEMPT_COLUMNS)
    return frame if strict is False else frame[frame["verified"]].reset_index(drop=True)


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
