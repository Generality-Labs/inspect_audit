---
name: reading-logs
description: Read Inspect AI eval logs (`.eval`/`.json`) with the Python API -- the header, per-sample summaries, one sample, or a stream of samples -- cheaply and without unzipping them. Use before opening any log.
---

# Reading Inspect eval logs

Everything goes through `inspect_ai.log`; for tables across many samples or logs see
`analyzing-logs`. The API reference is https://inspect.aisi.org.uk/eval-logs.html.md if
you need more than this.

## The right API: `inspect_ai.log`

All log reading goes through `inspect_ai.log`. Pick the cheapest function that answers the question:

| Function | Use when |
|---|---|
| `list_eval_logs(path)` | You need to enumerate log files in a directory (recursive by default). |
| `read_eval_log(log_file, header_only=True)` | You only need task/model/status/config/aggregated results. Skips all sample data. **Fastest by far.** |
| `read_eval_log_sample_summaries(log_file)` | You need per-sample IDs and scores but not transcripts. Orders of magnitude faster than reading full samples. |
| `read_eval_log_sample(log_file, id=N)` | You need exactly one sample's full content. |
| `read_eval_log_samples_by_id(log_file, ids)` | You need a few named samples, not the whole log. |
| `read_eval_log_samples(log_file)` | Generator over all samples. Use when you genuinely need full transcripts. Pass `all_samples_required=False` if the log status isn't `success` (cancelled or errored runs). |
| `read_eval_log(log_file)` | You really do need the whole log as one object. Last resort for big logs. |

```python
from inspect_ai.log import (
    list_eval_logs,
    read_eval_log,
    read_eval_log_sample,
    read_eval_log_samples,
    read_eval_log_sample_summaries,
)

# Enumerate logs in a directory
logs = list_eval_logs("logs/")

# Metadata only (cheapest)
header = read_eval_log("logs/run.eval", header_only=True)
print(header.eval.task, header.eval.model, header.status, header.results)

# Per-sample summaries (cheap, no transcripts)
for summary in read_eval_log_sample_summaries("logs/run.eval"):
    print(summary.id, summary.scores)

# Stream full samples (only when you actually need transcripts)
for sample in read_eval_log_samples("logs/run.eval"):
    process(sample)

# Stream even from a cancelled or errored log
for sample in read_eval_log_samples("logs/incomplete.eval", all_samples_required=False):
    process(sample)
```

## Never unzip `.eval` files

`.eval` files are zip archives, but their layout is Inspect's implementation detail and
changes between versions. Read the header with `read_eval_log(path, header_only=True)`
(it has `eval.config.epochs`, task, model, status) instead of opening `header.json`.

## Size before you read

Headers and sample summaries are small; loop over them freely. A full sample can be
much bigger in memory than on disk, so stream with `read_eval_log_samples()` rather than
loading whole logs, and pass `exclude_fields={"events"}` (or `attachments`) when you
only need messages and scores. Check `ls -lh` first on a large set.

## Traps in the logs you are given

- **Sliced logs keep the whole run's header.** A log sliced to one item still carries
  the full run's `results` (accuracy over every sample). Take an item's outcome from its
  own sample `scores`, never from the header's headline.
- **`eval.config` is what the run was told, not everything it did.** Retries,
  `config_updates` and overridden solvers show in the sample and the plan; read
  `log.plan` for what actually ran.
- **Non-success logs:** pass `all_samples_required=False` when streaming from a log whose
  status is `error`, `cancelled` or `started`.

## Investigating errors

Three common error questions, with the right route for each:

- **"Why did the task fail?"** Start at the header for overall status, then walk summaries to find which samples errored:

  ```python
  header = read_eval_log("logs/run.eval", header_only=True)
  print(header.status)          # "success" / "error" / "cancelled"
  print(header.error)            # top-level error (if the eval itself crashed)

  for s in read_eval_log_sample_summaries("logs/run.eval"):
      if s.error is not None:
          print(s.id, s.error)   # samples that errored
  ```

  Drill into a specific failing sample with `read_eval_log_sample(path, id=...)` and inspect `sample.error` for traceback/message and `sample.messages` for the transcript leading up to it.

- **"Why is this specific sample wrong (debugging)?"** Read just that sample and inspect its fields:

  ```python
  s = read_eval_log_sample("logs/run.eval", id=N)
  print(s.error)      # set if the sample errored out
  print(s.messages)   # full conversation
  print(s.scores)     # scorer outputs + explanations
  print(s.events)     # fine-grained events: tool calls, model calls, scoring
  ```

  The `events` field is the highest-resolution view of what actually happened step by step.

## Quick decision tree

- "What model/task/config was this run?" -> `read_eval_log(path, header_only=True)`
- "Which samples failed or scored low?" -> `read_eval_log_sample_summaries(path)`, filter on `s.error` / `s.scores`
- "Show me sample 42 in detail" -> `read_eval_log_sample(path, id=42)`
- "These five samples" -> `read_eval_log_samples_by_id(path, [...])`
- "Process every sample" -> `for s in read_eval_log_samples(path): ...`
- "Compare or aggregate across logs" -> `analyzing-logs`
