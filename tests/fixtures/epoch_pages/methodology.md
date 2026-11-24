# epoch-rubric v1 (draft)

Form text, option lists and numeric fields transcribed from Epoch's review form (Rubric v1). Guidance paraphrases Epoch's intent and adds Generality Labs' decision rule: Pass is the default; a Flag needs a measured impact on the headline results.

## Verdict

NEI when reviewability is Inadequate; Flawed when any minimum-standard check is Flag; Incomplete when reviewability or any minimum-standard check is Not Reviewed; Verified otherwise. Quality checks never change the verdict.

the rubric stops once reviewability is Inadequate, and at the first Flag in rubric order: the minimum-standard checks after it and every quality check are Not Reviewed, as the published rubric's "stop" reads; the Flag is reported with its impact and the review publishes as Flawed

a Flag on a benchmark-wide check carries an impact statement, the measured effect on the headline results with numbers, at most 60 words, which the page prints as a footnote under the row's note; a scoring Flag carries one only when logs were on hand, and no Pass carries one

## Sampling

Unit: task. Population: every task in the benchmark version reviewed. Sample: all tasks when the benchmark has at most 50; otherwise 50, random, stratified by category when the benchmark has categories

Measure `sampled_defect_rate` = DEFECT / DEFECT + NO_ISSUE_FOUND. UNRESOLVED and NOT_ASSESSED are reported beside the rate, not counted

## Checks

Rate each check in `auditor.ratings` under its id, with its level as the label.

### reviewability (1. Reviewability): Reviewability

Can the tasks, scoring logic and harness settings used for each model be inspected?

Scale: Full, Partial, Inadequate, Not Reviewed

Establish what can be inspected before any other work: the tasks, the scoring logic, and the harness and API settings each model ran with (reasoning effort, token and time limits, tool access, system prompts). Read these from log headers and source, not from documentation alone. Inadequate stops the review at NEI: record the level with evidence, leave the other checks Not Reviewed, and publish the limited conclusion.

### scoring_integrity (2. Scoring (minimum standard)): Scoring

Are tasks answerable as written, is the scorer accurate, and does the benchmark measure the claimed capability?

Scale: Pass, Flag, Not Reviewed. threshold: flag when sampled_defect_rate >= 0.20

Sample tasks under the sampling policy, or label the whole population. A task is defective when it cannot be answered correctly as written, when the scorer rejects a correct answer, or when it accepts an incorrect one. Report numerator, denominator and selection method. Flag at or above the threshold, or on demonstrated grading corruption at scale. Authored attacks on the grader and single false negatives are limitations, not Flags; say which kind each defect is. The row is decided by the item audits, with no attempts run for it: in phase 1 the question-labels audit judges each sampled item as written, with no logs read; in phase 2 the grading-labels audit judges the same items' recorded grades from the logs, and the measure is their union, an item defective as written or as graded counting once. The note is the defect rate against the threshold in one sentence, without the counts the prevalence line already prints. A Flag with logs on hand carries an impact, the recorded submissions the defects misgraded, printed as a footnote; a Pass carries none.

### benchmark_consistency (2. Scoring (minimum standard)): Benchmark consistency

Have the scorer, instructions or ground truth changed without a version bump?

Scale: Pass, Flag, Not Reviewed. a Flag needs `impact`

Phase 1 answers this from the source's version history, its changelog and the published methodology, and is the last row phase 1 writes: every evaluation-quality row waits for phase 2. Phase 2 re-checks it against the versions and graders the logs record. Compare the scorer, instructions and reference answers across the logs' recorded versions and grading models against what the leaderboard presents as one series. Flag only when results from different versions are shown to be incomparable, with a measured score difference attributable to the change. An unversioned change with no measured effect is a limitation.

### elicitation_adequacy (2. Scoring (minimum standard)): Elicitation

Is model elicitation so constraining that it substantially reduces performance against reasonable alternatives?

Scale: Pass, Flag, Not Reviewed. needs logs (phase 2); a Flag needs `impact`

Compare the recorded limits and settings with what the tasks need and with reasonable alternatives. A deliberately constrained design (single turn, no tools) passes when the constraint is the point of the benchmark. Flag only when the benchmark as a whole under-elicits, meaning the best listed configuration of the affected models is still depressed. Rows that are poor but have a better row for the same model are a limitation.

### evaluation_bias (2. Scoring (minimum standard)): Bias in evaluation setup

Does the setup give any model a material advantage the others do not get?

Scale: Pass, Flag, Not Reviewed. needs logs (phase 2); a Flag needs `impact`

Look for prompts, tools, budgets, graders or termination rules that differ by model or developer. Flag only on a demonstrated model-specific advantage, not on heterogeneous settings alone.

### resource_adequacy (3. Evaluation quality): Elicitation and resource adequacy

Elicitation and resource adequacy: Are the resources given to models (reasoning token/turn budget, tool access, etc.) sufficient for them to perform near their ceiling?

Scale: Sufficient, Constraining, Unreasonably constraining, Unknown, Not Reviewed. needs logs (phase 2)

Judge whether the resources given to models let them perform near their ceiling: reasoning and output budgets, turns, tools. Count attempts cut off at a limit and say which configurations lose answers to it.

### scaffold_fairness (3. Evaluation quality): Scaffold fairness

Scaffold fairness: What scaffold does the leaderboard report?

Scale: Shared common scaffold, Mix of model-specific and common scaffolds, Model-specific scaffolds, Not Reviewed. needs logs (phase 2)

State what scaffold the leaderboard reports: one common prompt and harness for every model, a mix, or model-specific scaffolds. Settings that vary per model (effort, caps) are reported under elicitation and bias, not here.

### contamination (3. Evaluation quality): Contamination

Is there evidence/risk of contamination?

Scale: Assessed, Not Reviewed. needs logs (phase 2); fields: as_of, tasks_public_pct, solutions_public_pct

State whether the tasks and their solutions are public, as of the review date, and whether there is evidence of training exposure. Public availability is exposure risk, not evidence of memorisation; say which was tested.

### human_completability (3. Evaluation quality): Human completability

Has human completability been assessed?

Scale: All tasks, Representative set of tasks, Poor implementation, Not established, Not Reviewed. needs logs (phase 2)

State whether a human baseline exists, on all tasks or a representative sample, and whether its design is sound (representative tasks, reasonable participants).

### score_range (3. Evaluation quality): Score range

Score range (if possible to estimate)

Scale: Estimated, Not Reviewed. needs logs (phase 2); fields: floor, ceiling

Give the floor (chance or a trivial strategy) and the ceiling (what a perfect or engine-assisted solver reaches, and what limits it), with the best observed score for context.

### statistical_adequacy (3. Evaluation quality): Statistical adequacy

Statistical adequacy: How many runs/model (≥ 5 recommended for error bars)

Scale: Known, Unknown, Not Reviewed. needs logs (phase 2); fields: runs_per_model

Give runs per model and the resulting standard error. Five or more runs per model is the recommended standard; single runs mean differences within about twice the standard error are noise.

### construct_validity (3. Evaluation quality): Construct validity

Construct validity

Scale: Measures stated capabilities, Partially measures stated capabilities, Does not measure stated capabilities, Not Reviewed. needs logs (phase 2)

Assessed last, after every other row, with everything the review learned in hand. Say what the score measures and how far that matches the capability the benchmark claims. Independent checks of the reference answers belong here, as does any evidence that the task can be solved without the claimed capability.

## Prose blocks

Written after submission in `/workspace/report/prose.json`.

The page shows, after its header, the rubric, then `representative_errors`, then `error_examples`, then the notices, and ends there. The other blocks below are not rendered: leave them out. One written anyway is checked against its limits and kept in prose.json.

### summary (not rendered; optional)

### methodology (not rendered; optional)

### interpretation (not rendered; optional)

### task_analysis (not rendered; optional)

### elicitation_and_scaffolding (not rendered; optional)

### representative_errors (bullets; shown for Verified, Flawed, NEI, Incomplete; required for Flawed)

One bullet per Epoch defect class present in the errors table (impossible as written, false negative, false positive), in Epoch's order: name the class as the table's group heading names it, say how many rows it covers and which sub-reasons they are, and give one or two of those rows as examples described by what is wrong, not by id. A run-level error gets its own bullet. The page opens with the rubric and follows it directly with these bullets, the errors table and the competing answers with their sources, so the bullets are the reader's first account of what is wrong. Nothing else: no definitions of the classes, no caveats about unresolved or unassessed items, no sampling notes; those belong in the scoring row.

Limits: at most sentences 3, at most items 5

### error_examples (table; shown for Verified, Flawed, NEI, Incomplete; required for Flawed)

Every recorded error. One row for each item coverage.json records as a defect, with Task naming its id, then any run-level errors; Error type is the defect in a few words, printed only for a row whose item has no recorded class (the page groups rows by each item's Epoch class and prints its sub-reason from the findings in this column); Notes is one plain sentence saying what is wrong with the item, as its reader would see it, with no account of how that was found (start from the auditor's headline); Affected logs names the runs or models the error touches, with a count, taken from that item's own grading-labels record (its scoring_effect and misgraded). Revise a row by its task id, never by its position in the list: the preview reports rows that share a run of words. The ids audited without a defect are listed under the table from coverage.json, and each item's competing answers with their sources are printed under it from the findings (the investigators' `sources`, or your review's); you write neither, and no URLs in the table.

Limits: at most cell words 30

### limitations (not rendered; optional)

### recommendations (not rendered; optional)

### footnotes

Evidence detail that supports a sentence on the page without cluttering it: counts with their denominators, model names and settings, dates, versions, locators. Refer to a footnote as [^n] from a representative-errors bullet or a rubric note (the page prints only the footnotes it refers to); a Flag's impact is appended as a footnote for you. A footnote supports a claim already made; it never introduces one.

Limits: at most 12 notes of 60 words
