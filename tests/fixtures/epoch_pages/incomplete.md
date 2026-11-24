# SimpleQA Verified Review

Benchmark: SimpleQA Verified  
Benchmark creator: Google  
Verdict: Incomplete  
Review date: 24th November 2026

## Benchmark Review Rubric

Rubric v1. We expect future revisions to account for new types of benchmark flaws.

### 1. Reviewability

| Level | Meaning | Status | Notes |
|---|---|---|---|
| Full | All tasks and scoring logic inspectable, and harness/API settings used for each model (reasoning effort, token/time limits, tool access, system prompts) are fully disclosed | [proceed to 2] |  |
| **Partial** | A representative sample of tasks and scoring logic inspectable, with full harness/API settings disclosed | **[proceed to 2]** | Checked and found so. |
| Inadequate | Limited, biased, or no inspectable tasks or scoring logic, harness/API settings undisclosed | [stop → NEI] |  |

### 2. Scoring

This is the minimum standard required to be Verified; failing any of these items results in a Flawed verdict.

Scoring prevalence: 1 of 10 inspected tasks (10%); threshold 20%.

| Defect Class | Examples | Default Threshold for Flawed | Status | Notes |
|---|---|---|---|---|
| Scoring | • Essentially impossible to correctly answer as written (e.g. underspecified task, hidden requirement, missing file/tool)<br>• False Negative (e.g. overly strict scorer, stale/incorrect ground truth, dependent on live external state that can drift, sandbox failure independent of the agent)<br>• False Positive (e.g. lax scorer, reward-hackable environment, stated skill can be bypassed via a shortcut such as exploiting an error in the scoring logic or retrieving the answer from the harness/web)<br>• Task egregiously doesn't measure the claimed capability | ≥20% of inspected sample contains errors or issue that corrupts grading at scale | **Pass**<br>Flag [stop → Flawed]<br>Not Reviewed | Checked and found so. |
| Benchmark consistency | • Scorer, instructions, or ground truth changed without a version bump | Leaderboard has incomparable results from different versions | **Pass**<br>Flag [stop → Flawed]<br>Not Reviewed | Checked and found so. |
| Elicitation | • Model elicitation is extremely constraining and is not the focus of the benchmark:<br>• Under-resourced relative to task size (token/turn/time limit, sandbox resources)<br>• Poor context management<br>• Lack of agentic environment where one would be natural to provide<br>• Excessive non-voluntary termination for agentic benchmarks | Substantially reduced performance compared to reasonable alternatives for the tasks | Pass<br>Flag [stop → Flawed]<br>**Not Reviewed** |  |
| Bias in evaluation setup | • Uneven compute/token budgets<br>• Unfair scaffold choice (e.g. only a subset of models optimized) | Material model-specific advantage found | Pass<br>Flag [stop → Flawed]<br>**Not Reviewed** |  |

### 3. Evaluation Quality

This is the standard we would like all benchmarks to meet, but it is not necessarily disqualifying to omit or fail these items.

| Question | Status | Notes |
|---|---|---|
| Elicitation and resource adequacy: Are the resources given to models (reasoning token/turn budget, tool access, etc.) sufficient for them to perform near their ceiling? | Sufficient<br>Constraining<br>Unreasonably constraining<br>Unknown<br>**Not Reviewed** |  |
| Scaffold fairness: What scaffold does the leaderboard report? | Shared common scaffold<br>Mix of model-specific and common scaffolds<br>Model-specific scaffolds<br>**Not Reviewed** |  |
| Is there evidence/risk of contamination? | As of ___: ___% of tasks public<br>___% of solutions public<br>**Not Reviewed** |  |
| Has human completability been assessed? | All tasks<br>Representative set of tasks<br>Poor implementation (Unrepresentative set of tasks, unreasonable set of participants)<br>Not established<br>**Not reviewed** |  |
| Score range (if possible to estimate) | ___ floor<br>___ ceiling<br>**Not Reviewed** |  |
| Statistical adequacy: How many runs/model (≥ 5 recommended for error bars) | ___ runs/model<br>Unknown<br>**Not Reviewed** |  |
| Construct validity | Measures stated capabilities<br>Partially measures stated capabilities<br>Does not measure stated capabilities<br>**Not Reviewed** |  |

## Representative Errors

The following errors were found in specific tasks. They do not cross our threshold for a Flawed verdict.

- Impossible as written (1 row): the key accepts one of two defensible years.

| Task | Error type | Notes | Affected logs |
|---|---|---|---|
| s0 | Impossible as written | The question does not say whether the first EP counts as an album. | Not assessed; no logs supplied |

Audited without finding a defect (9): s1, s2, s3, s4, s5, s6, s7, s8, s9

*Phase 1 only: the tasks and their scoring were audited without evaluation logs. Elicitation, bias in the evaluation setup, the evaluation-quality section and the effect of the recorded defects on published results await an audit given the logs, so the verdict cannot be Verified.*
