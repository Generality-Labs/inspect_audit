---
name: epoch-rubric
description: Epoch AI's benchmark review form (Rubric v1), its rows and statuses, and how the verdict follows from them. Read before rating.
---

Source: https://epoch.ai/data/benchmark-reviews-documentation/methodology. Form text, option lists and numeric fields transcribed from Epoch's review form (Rubric v1). Guidance paraphrases Epoch's intent and adds Generality Labs' decision rule: Pass is the default; a Flag needs a measured impact on the headline results.

## Rating

Rate every row of Epoch's form in auditor.ratings, keyed by the row id and labelled with one of its statuses, as the epoch-rubric skill says. Stop at the first Flag: every row after it is Not Reviewed. Give each broader finding the id of its row as its criterion. The verdict is derived from the rows; do not write one.

Verdict: NEI when reviewability is Inadequate; Flawed when any minimum-standard check is Flag; Incomplete when reviewability or any minimum-standard check is Not Reviewed; Verified otherwise. Quality checks never change the verdict.

Sampling: all tasks when the benchmark has at most 100; otherwise 50, random, stratified by category when the benchmark has categories

## Rows

### `reviewability`: Reviewability (1. Reviewability)

Can the tasks, scoring logic and harness settings used for each model be inspected?

Statuses: Full, Partial, Inadequate, Not Reviewed.

Establish what can be inspected before any other work: the tasks, the scoring logic, and the harness and API settings each model ran with (reasoning effort, token and time limits, tool access, system prompts). Read these from log headers and source, not from documentation alone. Inadequate stops the review at NEI: record the level with evidence, leave the other checks Not Reviewed, and publish the limited conclusion.

### `scoring_integrity`: Scoring (2. Scoring (minimum standard))

Are tasks answerable as written, is the scorer accurate, and does the benchmark measure the claimed capability?

Statuses: Pass, Flag, Not Reviewed.

Sample tasks under the sampling policy, or label the whole population. A task is defective when it cannot be answered correctly as written, when the scorer rejects a correct answer, or when it accepts an incorrect one. Report numerator, denominator and selection method. Flag at or above the threshold, or on demonstrated grading corruption at scale. Authored attacks on the grader and single false negatives are limitations, not Flags; say which kind each defect is. The row is decided by item verdicts: send investigators with the scoring checks (the threshold's `checks`) over the sample, and review what they flag with auditor.reviews (your review is the take that stands). The rate is computed from those verdicts, not written: an item is a defect when its standing verdict on any scoring check is a grade that check flags, sound when every settled verdict is unflagged, and unresolved otherwise. Say in the note how the sample was selected. A status that disagrees with the rate starts its explanation with "Threshold override:" and says why. A Flag with logs on hand states the impact, the recorded submissions the defects misgraded (a regrade job measures it); a Pass carries none.

### `benchmark_consistency`: Benchmark consistency (2. Scoring (minimum standard))

Have the scorer, instructions or ground truth changed without a version bump?

Statuses: Pass, Flag, Not Reviewed.

Answer from the benchmark's version history (the snapshot in /inputs has none: clone the repository seed.json names into /workspace and read its log for the task's files), its changelog and published methodology, then check it against the versions and graders the supplied logs record. Compare the scorer, instructions and reference answers across the logs' recorded versions and grading models against what the leaderboard presents as one series. Flag only when results from different versions are shown to be incomparable, with a measured score difference attributable to the change. An unversioned change with no measured effect is a limitation.

### `elicitation_adequacy`: Elicitation (2. Scoring (minimum standard))

Is model elicitation so constraining that it substantially reduces performance against reasonable alternatives?

Statuses: Pass, Flag, Not Reviewed.

Compare the recorded limits and settings with what the tasks need and with reasonable alternatives. A deliberately constrained design (single turn, no tools) passes when the constraint is the point of the benchmark. Flag only when the benchmark as a whole under-elicits, meaning the best listed configuration of the affected models is still depressed. Rows that are poor but have a better row for the same model are a limitation.

### `evaluation_bias`: Bias in evaluation setup (2. Scoring (minimum standard))

Does the setup give any model a material advantage the others do not get?

Statuses: Pass, Flag, Not Reviewed.

Look for prompts, tools, budgets, graders or termination rules that differ by model or developer. Flag only on a demonstrated model-specific advantage, not on heterogeneous settings alone.

### `resource_adequacy`: Elicitation and resource adequacy (3. Evaluation quality)

Elicitation and resource adequacy: Are the resources given to models (reasoning token/turn budget, tool access, etc.) sufficient for them to perform near their ceiling?

Statuses: Sufficient, Constraining, Unreasonably constraining, Unknown, Not Reviewed.

Judge whether the resources given to models let them perform near their ceiling: reasoning and output budgets, turns, tools. Count attempts cut off at a limit and say which configurations lose answers to it.

### `scaffold_fairness`: Scaffold fairness (3. Evaluation quality)

Scaffold fairness: What scaffold does the leaderboard report?

Statuses: Shared common scaffold, Mix of model-specific and common scaffolds, Model-specific scaffolds, Not Reviewed.

State what scaffold the leaderboard reports: one common prompt and harness for every model, a mix, or model-specific scaffolds. Settings that vary per model (effort, caps) are reported under elicitation and bias, not here.

### `contamination`: Contamination (3. Evaluation quality)

Is there evidence/risk of contamination?

Statuses: Assessed, Not Reviewed.

State whether the tasks and their solutions are public, as of the review date, and whether there is evidence of training exposure. Public availability is exposure risk, not evidence of memorisation; say which was tested.

### `human_completability`: Human completability (3. Evaluation quality)

Has human completability been assessed?

Statuses: All tasks, Representative set of tasks, Poor implementation, Not established, Not Reviewed.

State whether a human baseline exists, on all tasks or a representative sample, and whether its design is sound (representative tasks, reasonable participants).

### `score_range`: Score range (3. Evaluation quality)

Score range (if possible to estimate)

Statuses: Estimated, Not Reviewed.

Give the floor (chance or a trivial strategy) and the ceiling (what a perfect or engine-assisted solver reaches, and what limits it), with the best observed score for context.

### `statistical_adequacy`: Statistical adequacy (3. Evaluation quality)

Statistical adequacy: How many runs/model (≥ 5 recommended for error bars)

Statuses: Known, Unknown, Not Reviewed.

Give runs per model and the resulting standard error. Five or more runs per model is the recommended standard; single runs mean differences within about twice the standard error are noise.

### `construct_validity`: Construct validity (3. Evaluation quality)

Construct validity

Statuses: Measures stated capabilities, Partially measures stated capabilities, Does not measure stated capabilities, Not Reviewed.

Assessed last, after every other row, with everything the review learned in hand. Say what the score measures and how far that matches the capability the benchmark claims. Independent checks of the reference answers belong here, as does any evidence that the task can be solved without the claimed capability.
