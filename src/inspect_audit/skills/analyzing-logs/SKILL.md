---
name: analyzing-logs
description: Build pandas tables across many Inspect samples or logs with `inspect_ai.analysis` (samples_df, evals_df, messages_df, events_df) -- score distributions, comparisons across models or runs, outliers -- and the dtype and score-column traps. Use for any question about more than one sample.
---

# Analyzing Inspect eval logs

For one log header or one sample, use `reading-logs`. For anything across samples or
runs, build a dataframe with `inspect_ai.analysis`, choosing the cheapest granularity
that answers the question:

| Builder | Output | Use when |
|---|---|---|
| `evals_df(logs)` | 1 row per log file | per-run rollups: which model scored highest, how long each run took |
| `samples_df(logs)` | 1 row per sample | per-sample analysis across runs: score distributions, outliers, comparisons |
| `messages_df(logs)` | 1 row per message | message-content slicing: filter by role/function, count tool calls |
| `events_df(logs)` | 1 row per event | event-level timing or transcript reconstruction |

```python
from inspect_ai.analysis import samples_df, EvalModel, SampleSummary, SampleScores
df = samples_df("/audit/logs", columns=EvalModel + SampleSummary + SampleScores)
```

**Composable columns.** Pick the granularity (one of the four `*_df` builders) and the columns you need from the pre-built groups (composed via `+`). Pre-built groups cover most needs:

- Eval-level: `EvalInfo`, `EvalTask`, `EvalModel`, `EvalDataset`, `EvalConfiguration`, `EvalResults`, `EvalScores`
- Sample-level: `SampleSummary`, `SampleScores`, `SampleMessages`
- Message-level: `MessageContent`, `MessageToolCalls`
- Event-level: `EventInfo`, `EventTiming`, `ModelEventColumns`, `ToolEventColumns`

Custom columns inherit from `EvalColumn` / `SampleColumn` / `MessageColumn` / `EventColumn`.

**Common args.** `parallel=True` for full-content reads (capped at 8 workers; not available on `evals_df`). `strict=False` returns `(df, errors)` so partial failures don't raise; always unpack the tuple: `df, errors = samples_df(..., strict=False)`.

**`prepare(df, [...])`** chains post-build operations: `model_info` (add org, display name, release date), `task_info` (task display names), `log_viewer` (local paths to URLs), `frontier` (mark top performers per task), `score_to_float` (score column conversion). Reach for it when enriching dataframes for plots or leaderboards.

**Three traps to know about up front:**

- **Score columns: single-scorer vs multi-scorer.** `samples_df` explodes scores into one column per scorer (`score_<scorer-name>`). For a one-scorer eval, group by that single column and aggregate. For multi-scorer / cross-scorer comparisons, prefer **`evals_df` with `EvalScores`** (gives you `score_headline_value` per log) and accept that you're averaging across scorers. Values can be strings (`'C'`/`'I'`), numbers, or nested dicts depending on the scorer; use `prepare(df, [score_to_float("score_headline_value")])` for eval headline scores, or pass the concrete sample score column(s) such as `score_<scorer-name>` when normalizing `samples_df`.
- **pyarrow dtypes bite scalar comparisons.** `samples_df` returns columns with pyarrow-backed dtypes (`double[pyarrow]`, `large_string[pyarrow]`). Plain `df[col].isna()` works, but scalar `==` on `<NA>` raises `TypeError: boolean value of NA is ambiguous`. Use `pd.to_numeric(df[col], errors='coerce')` or `df[col].fillna(...)` before comparing.
- **Status field canonical values.** `EvalLog.status ∈ {success, error, cancelled, started}`. `started` means the run crashed mid-execution. Filter on these explicitly rather than guessing.

The column reference is https://inspect.aisi.org.uk/dataframe.html.md if you need more
than this.
