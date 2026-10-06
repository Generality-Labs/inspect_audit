# Epoch AI benchmark review methodology

The review is a decision procedure in three stages. Stage 1 is a gate. Stage 2 is the
minimum standard: failing any item results in a Flawed verdict. Stage 3 is the standard
all benchmarks should meet, but omitting or failing these items is not disqualifying.

Record each row in `review.json`. The verdict is derived from the rows by `check_report`
and `publish_report`; it is not written by the reviewer.

## 1. Reviewability

| Level | Meaning | Outcome |
|---|---|---|
| Full | All tasks and scoring logic inspectable, and harness/API settings used for each model (reasoning effort, token/time limits, tool access, system prompts) are fully disclosed | proceed to 2 |
| Partial | A representative sample of tasks and scoring logic inspectable, with full harness/API settings disclosed | proceed to 2 |
| Inadequate | Limited, biased, or no inspectable tasks or scoring logic; harness/API settings undisclosed | stop: verdict NEI (Not Enough Information) |

## 2. Scoring (minimum standard)

Each defect class is graded Pass, Flag or Not Reviewed. Any Flag stops the review at
Flawed. A Not Reviewed row leaves the verdict Incomplete.

| Defect class | Examples | Default threshold for Flag |
|---|---|---|
| Scoring | Essentially impossible to answer correctly as written (underspecified task, hidden requirement, missing file or tool). False negatives (overly strict scorer, stale or incorrect ground truth, dependence on live external state that can drift, sandbox failure independent of the agent). False positives (lax scorer, reward-hackable environment, the stated skill can be bypassed via a shortcut such as exploiting an error in the scoring logic or retrieving the answer from the harness or web). The task egregiously does not measure the claimed capability. | At least 20% of the inspected sample contains errors, or an issue corrupts grading at scale |
| Benchmark consistency | Scorer, instructions or ground truth changed without a version bump | The leaderboard holds incomparable results from different versions |
| Elicitation | Model elicitation is extremely constraining and is not the focus of the benchmark: under-resourced relative to task size (token, turn or time limit, sandbox resources); poor context management; lack of an agentic environment where one would be natural; excessive non-voluntary termination for agentic benchmarks | Substantially reduced performance compared to reasonable alternatives for the tasks |
| Bias in evaluation setup | Uneven compute or token budgets; unfair scaffold choice (only a subset of models optimised) | A material model-specific advantage is found |

The scoring row's prevalence is computed from `coverage.json`: questions labelled DEFECT
over questions with a resolved label (DEFECT plus NO_ISSUE_FOUND). Unresolved and
unassessed questions are reported beside it, not counted either way. A status that
disagrees with the computed threshold needs a written `threshold_override_reason`.

## 3. Evaluation quality

| Question | Statuses |
|---|---|
| Elicitation and resource adequacy: are the resources given to models (reasoning token, turn budget, tool access) sufficient for them to perform near their ceiling? | Sufficient; Constraining; Unreasonably constraining; Unknown; Not Reviewed |
| Scaffold fairness: what scaffold does the leaderboard report? | Shared common scaffold; Mix of model-specific and common scaffolds; Model-specific scaffolds; Not Reviewed |
| Is there evidence or risk of contamination? | Assessed, with fields `as_of`, `tasks_public_pct`, `solutions_public_pct`; Not Reviewed |
| Has human completability been assessed? | All tasks; Representative set of tasks; Poor implementation (unrepresentative set of tasks, unreasonable set of participants); Not established; Not Reviewed |
| Score range, if possible to estimate | Estimated, with fields `floor`, `ceiling`; Not Reviewed |
| Statistical adequacy: how many runs per model? (at least 5 recommended for error bars) | Known, with field `runs_per_model`; Unknown; Not Reviewed |
| Construct validity | Measures stated capabilities; Partially measures stated capabilities; Does not measure stated capabilities; Not Reviewed |

## Verdicts

- **NEI**: reviewability is Inadequate.
- **Flawed**: any minimum-standard defect class is flagged.
- **Verified**: reviewability is Full or Partial and every minimum-standard class passes.
- **Incomplete**: the review stopped short of a verdict (a gate or defect class is Not
  Reviewed). Not an Epoch outcome; it records that the work is unfinished.
