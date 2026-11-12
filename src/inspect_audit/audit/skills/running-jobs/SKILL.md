---
name: running-jobs
description: Launch, wait on and collect benchmark and per-item investigation jobs. Read before launching one.
---

## Choose the work

Two kinds of job, each one tool call. `run_benchmark` runs the investigated benchmark for
the models you name and produces fresh attempts. `run_investigation` runs inspect_audit's
investigator over chosen benchmark items, one sample per item, optionally against
recorded attempts. The seed's `jobs` block lists the models each part may play:
subjects for benchmarks, investigators for investigations, judges for the benchmark's own graders.

Name a benchmark's judges explicitly with `model_roles`, and any judge the task
builds from its own arguments in `task_args`. An investigation's judge is the one its logs
record: bind that model under the same role, or the investigation refuses.

## The register

Start the register before collecting any investigation: start_register(task_args, checks) with
the arguments and checks your investigations use. Each collected investigation then adds its verdicts as
investigator takes, with the job, eval and epoch they came from. Work the flagged cells with
register(view="flagged"), and record your own reviews in findings.json under auditor.reviews.
Keep the original investigator results unchanged. Empty cells are work not done.

## Sources

An investigation's `logs` are labels of benchmark jobs you ran, or supplied sources from the
seed's `jobs.supplied_logs`. Verify dataset identity before joining attempts: the
investigation refuses a log whose question differs from the installed benchmark's (a run with
sample_shuffle numbers its samples after shuffling, so its ids name different
questions).

## Smoke, inspect, expand

Prove a configuration on one or two samples first. Check the candidate, tools, scorer
and outputs all worked before scaling. `jobs(action="wait")` blocks without spending
tokens; `jobs(action="log")` shows the runner's own log, where install failures and
crashes appear; `jobs(action="collect")` brings a finished job's logs to
/inputs/jobs/<label>/ and summarises each. `jobs(action="list")` shows what you have
launched: check it before launching more.

## Money

When configured to share one capped OpenRouter key, you and your jobs draw on that
key; `budget()` reports its remaining allowance. Other provider routes have separate
credentials and limits. The key balance is not a per-audit usage breakdown. An investigation's `item_cost_limit` caps one item. Bound a
grader's `max_tokens` and check what a smoke job recorded before expanding. Scale in
bounded batches.

A missing credential, malformed request or broken investigation image is an investigation limitation.
Record what it prevented, repair once if feasible, and avoid retry loops. Attribute a
failure to the benchmark only after ruling out our configuration and reconstruction.

Allocate the available budget yourself, including subsequent publication. Run useful
experiments before submitting findings; there is no fixed reporting reserve.

Before submit_findings, finish or stop every job and collect its logs. Job tools
disappear after submission; publishing uses only existing evidence and calculations.
