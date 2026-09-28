# Vendored skills

`reading-logs` and `analyzing-logs` derive from
[meridianlabs-ai/inspect-skills](https://github.com/meridianlabs-ai/inspect-skills),
MIT licensed, Copyright (c) 2026 Meridian Labs. The licence is in `LICENSE.meridian`.
Vendored 2026-08-17; adapted 2026-09-28, so they are no longer verbatim copies.

What was changed on 2026-09-28, for a sandboxed auditor with no REPL, notebook or Scout:
- `analyzing-logs`: kept the `inspect_ai.analysis` builders, column groups, `prepare()`
  and the score-column, pyarrow and status traps; removed the tool-routing section, the
  Scout, REPL, notebook and log-directory guidance, and `scripts/append_to_notebook.py`.
- `reading-logs`: kept the API table, the never-unzip rule and error investigation;
  added `read_eval_log_samples_by_id`, `exclude_fields` and the sliced-log traps (a
  sliced log keeps the whole run's `results`); removed crashed-log recovery, trace-log
  diagnostics and the long resources section.
- `map-inspect-packages` is no longer vendored.

The removed text is in git history (before 2026-09-28) and upstream.
