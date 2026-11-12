---
name: running-jobs
description: Run the benchmark (as it is, rescored, or with changed code) and send per-item investigators; wait on and collect them. Read before launching one.
---

## Choose the work

Two different things.

`run_eval` runs the benchmark and gives you new evidence about it:
- fresh attempts by the models you name;
- `rescore`: the benchmark's own grader run again on attempts already recorded, `epochs`
  times each (epochs that disagree are judge noise; epochs that agree with each other
  but not with the recorded grade are a stable disagreement);
- `code`: either of the above with the benchmark changed, an ablation. Copy the files
  you want to change into a folder, at their paths inside the installed package
  (`inspect_evals/simpleqa/simpleqa.py`, not the repository's `src/...` path), edit them,
  and pass the folder. Every other file is the original. Test the change in your box
  first. The job records the diff; cite it in findings.

`send_investigators` sends one investigator per item, inside the benchmark's own setup,
against the checks you choose; their verdicts go into the register.

The task's arguments are fixed by the launch policy. The seed's `jobs` block lists the
models each part may play: subjects for fresh attempts, investigators, and judges for
the benchmark's own graders. Name a judge as `{"role": "model"}`; when rescoring or
investigating recorded attempts it must be the model those logs record.

## The register

The register lays out every permitted item against the policy's checks; it starts itself the
first time you view it or collect an investigation. Each collected investigation adds its verdicts as
investigator takes, with the job, eval and epoch they came from. Work the flagged cells with
register(view="flagged"), and record your own reviews in findings.json under auditor.reviews.
Keep the original investigator results unchanged. Empty cells are work not done.

## Sources

An investigation's `logs`, like `rescore`, are labels of jobs you ran, or supplied sources from the
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
credentials and limits. The key balance is not a per-audit usage breakdown. `item_cost_limit` caps one investigated or rescored item. Bound a
grader's `max_tokens` and check what a smoke job recorded before expanding. Scale in
bounded batches.

A missing credential, malformed request or broken investigation image is an investigation limitation.
Record what it prevented, repair once if feasible, and avoid retry loops. Attribute a
failure to the benchmark only after ruling out our configuration and reconstruction.

Allocate the available budget yourself, including subsequent publication. Run useful
experiments before submitting findings; there is no fixed reporting reserve.

Before submit_findings, finish or stop every job and collect its logs. Job tools
disappear after submission; publishing uses only existing evidence and calculations.
