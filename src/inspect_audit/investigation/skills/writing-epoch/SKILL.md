---
name: writing-epoch
description: Fill the Epoch AI benchmark review (reviewability gate, minimum-standard defect classes, evaluation-quality questions) from evidence; the verdict is derived, not chosen. Read before recording any review row.
---

## Contract

The deliverable is `report/epoch_review.md`, rendered by `check_report` and
`publish_report` from two records you maintain: `report/review.json` (the review rows,
schema in `report/review.schema.json`) and `report/findings.json` (the evidence
register). Edit the records, never the rendered markdown. `report/methodology.md`
defines every row; read it before planning. Do not substitute a thematic essay, a GL
nine-dimension report or any other format.

## The procedure is ordered

Work the stages in order and respect the stop rules.

1. **Reviewability** first. Establish what can be inspected: tasks, scoring logic, and
   the harness and API settings each model ran with (reasoning effort, token and time
   limits, tool access, system prompts). Read these from log headers and source, not
   from documentation alone. Inadequate stops the review: record the level with
   evidence, leave the other rows Not Reviewed, explain in `limitations`, and publish.
   Spending the budget on a benchmark that cannot be reviewed is a waste.
2. **Minimum standard** next. Each of the four defect classes is Pass, Flag or Not
   Reviewed, with evidence. A Flag anywhere makes the benchmark Flawed; keep working the
   remaining classes anyway, since the report must say what else was found.
3. **Evaluation quality** last. These inform the reader and do not change the verdict.
   A question you did not examine is Not Reviewed; do not infer an answer from silence.

## The scoring row and the 20% threshold

Prevalence must come from a sample selected for prevalence. Choose the questions for the
question-labels auditors with a recorded random seed, not because they looked suspicious;
a purposive set can establish that a defect exists but not how common it is. Record the
selection method and seed in the scoring row's notes. The prevalence is computed from
`report/coverage.json`: DEFECT over resolved labels, with unresolved and unassessed
counts beside it. `check_report` refuses a Pass or Flag that contradicts the computed
threshold unless `threshold_override_reason` says why (for example an issue below 20%
that still corrupts grading at scale, which the methodology also counts as a Flag).
Without a coverage file, give `inspected` and `with_defect` on the row and say how they
were obtained. A question-level defect is not automatically a misgraded submission.

## Benchmark-wide rows need benchmark-wide evidence

Consistency, elicitation and bias are not answered by item audits. Consistency compares
scorer, instructions and ground truth across the logs' recorded versions, grader models
and task arguments against what the leaderboard presents as one series. Elicitation
compares the recorded limits and resources with what the tasks need and with reasonable
alternatives. Bias looks for budgets or scaffolds that differ by model. Each needs its
own evidence references; a verdict on one item is not evidence for a row.

## Evidence and notes

Every assessed row carries at least one evidence reference: a path under `/inputs` or
in the report bundle, a location within it, and a quotation where it helps. Notes state
what was examined, the result, and what it changes about reading the score. Put
numerator, denominator and selection method beside every number. Distinguish measured
effects from hypothesised consequences. Clean rows still say what was checked.

The findings register follows `report/findings.schema.json`; `section` names the review
row a finding supports (`reviewability`, a defect class, or a quality question). Each
supported or qualified finding should be cited from its row's notes.

## Publication

Call `check_report`. It validates both records, derives the verdict into
`report/verdict.json` and renders `report/epoch_review.md`. Read the markdown and fix
the records until the derived verdict and the prevalence line say what the evidence
supports. Figures go under `report/evidence/` with their data tables and are referenced
from notes. Then call `publish_report`, which re-derives and saves the bundle. Never
write the verdict yourself, never invent an overall grade, and never leave `summary`
claiming more than the rows show.
