"""Choosing what to audit.

Attempts come from `inspect_ai.analysis.samples_df`, which reads log summaries in
parallel and emits one row per attempt with a column per scorer. Selection is then
ordinary dataframe work: `cand[cand.never_solved]`, `cand[cand.pass_rate < 0.1]`.

Task names are deliberately not used to decide which logs belong to a task: the same
benchmark is routinely logged under a different name (Epoch's SimpleQA Verified logs
are `simpleqa_verified`, two of them `bench/simpleqa_verified`, while the published
task resolves as `inspect_evals/simpleqa_verified`).
"""

from collections.abc import Collection

import pandas as pd
from inspect_ai.analysis import EvalModel, SampleSummary, samples_df
from inspect_ai.dataset import Sample
from inspect_ai.log import EvalLog
from inspect_ai.scorer import value_to_float

__all__ = ["attempts", "candidates", "is_pass", "score_columns"]

LogSource = str | list[str] | EvalLog | list[EvalLog]

_to_float = value_to_float()


def is_pass(value: object) -> bool:
    """Whether a score value counts as a pass.

    Uses Inspect's own `value_to_float`, the interpretation its metrics use, so `C`/`I`,
    boolean spellings and numeric strings mean here what they mean elsewhere.

    A missing score is not a pass. Attempts that errored have no score, and comparing
    a pyarrow `<NA>` raises rather than returning `False`, so it is screened here.

    Args:
        value: A score value, in whatever shape the scorer produced.
    """
    if value is None or (not isinstance(value, (list, dict)) and pd.isna(value)):
        return False
    return _to_float(value) >= 1.0  # type: ignore[arg-type]


def sample_id_of(sample: Sample, index: int) -> str:
    """The id Inspect will use for a sample: its own, else its 1-based position."""
    return str(sample.id if sample.id is not None else index)


def score_columns(frame: pd.DataFrame) -> list[str]:
    """The scorer columns of an attempts frame, one per scorer that ran."""
    return [column for column in frame.columns if str(column).startswith("score_")]


def attempts(
    logs: LogSource,
    *,
    sample_ids: Collection[str] | None = None,
    parallel: bool | int = True,
) -> pd.DataFrame:
    """One row per recorded attempt, with a column per scorer.

    Args:
        logs: Log directory, log files, or already-read `EvalLog`s.
        sample_ids: Restrict to these sample ids.
        parallel: Read logs in parallel.

    Returns:
        The `samples_df` frame for `logs`: `id`, `epoch`, `model`, `log`, `target`,
        one `score_*` column per scorer, plus error, limit and usage columns.
    """
    frame = samples_df(logs, columns=SampleSummary + EvalModel, parallel=parallel)
    if sample_ids is not None:
        wanted = {str(sample) for sample in sample_ids}
        frame = frame[frame["id"].astype(str).isin(wanted)]
    return frame.reset_index(drop=True)


def candidates(logs: LogSource, *, scorer: str | None = None) -> pd.DataFrame:
    """One row per sample, summarising how the field did on it.

    This is the frame you filter to choose what to audit.

    Args:
        logs: Log directory, log files, or already-read `EvalLog`s.
        scorer: Which scorer decides whether an attempt passed. Defaults to the only
            one; required when the logs carry several, since "solved" is otherwise
            ambiguous.

    Returns:
        Columns: `id`, `n_attempts`, `n_models`, `models`, `n_correct`, `pass_rate`,
        `n_errors`, `never_solved`, `all_solved`.

    Raises:
        ValueError: If `scorer` is not given and the logs carry more than one.
    """
    frame = attempts(logs)
    column = _score_column(frame, scorer)

    frame = frame.assign(
        correct=frame[column].map(is_pass),
        errored=frame["error"].astype(str).str.len() > 0,
    )
    summary = (
        frame.groupby("id")
        .agg(
            n_attempts=("model", "size"),
            n_models=("model", "nunique"),
            models=("model", lambda models: sorted(set(models))),
            n_correct=("correct", "sum"),
            pass_rate=("correct", "mean"),
            n_errors=("errored", "sum"),
        )
        .reset_index()
    )
    summary["never_solved"] = summary["n_correct"] == 0
    summary["all_solved"] = summary["n_correct"] == summary["n_attempts"]
    return summary


def _score_column(frame: pd.DataFrame, scorer: str | None) -> str:
    available = score_columns(frame)
    if scorer is not None:
        for candidate in (scorer, f"score_{scorer}"):
            if candidate in available:
                return candidate
        raise ValueError(f"no scorer {scorer!r} in these logs; found {', '.join(available)}")
    if len(available) != 1:
        raise ValueError(
            f"these logs carry {len(available)} scorers ({', '.join(available) or 'none'}); "
            "pass scorer= to say which one decides whether an attempt passed"
        )
    return available[0]
