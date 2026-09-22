---
name: investigating
description: Establish a benchmark's measurement claim, prioritise hypotheses and test them against code and recorded behaviour. Read at the start of an investigation.
---

## Orient

Before planning, read report/framework/Framework.tex, ScoringCriteria.tex and checks.json.
These define the scope, meanings and rating rubric. Plan evidence collection against the
actual question for each dimension/check; record what was examined, the result and remaining
gaps. For example, clustering concerns model-score discrimination, not dataset clustering;
unequal constraint effects concerns how a constraint binds different models. Related
observations can be findings without answering those checks. Do not assign a clean rating
merely because no worker reported a problem.


Establish what the benchmark claims to measure from the paper and README. Trace the
implementation: dataset, model-visible information, tools/environment, submission,
scorer and aggregation. Keep a short source-linked brief. Compare the pinned version
with the authoritative release; a historical defect may already be fixed.

Check that source, paper and logs describe comparable conditions. Read actual model,
judge, prompt, budget and metric configurations from log headers. Reproduce reported
numbers where the necessary evidence exists. An unexplained discrepancy remains a
limitation until you can establish its cause or bound its effect.

Inventory the population with sample summaries before loading whole transcripts.
Separate items, epochs, models and selected subsets. Count attempted/completed cases,
errors, unscored records, empty outputs and limit hits with explicit denominators.
Use reading-logs and analyzing-logs for the APIs; never unzip .eval files. A zero from
an extractor is not evidence of absence until it detects a known positive.

Then read selected transcripts across outcomes and configurations. Record observations
per attempt. Use them to generate hypotheses as well as checking hypotheses from code.
Inaccessible reasoning and inaccessible log sources are missing coverage.

## Prioritise and check

For each lead, state the suspected mechanism, what it would change about interpreting
the score, the strongest alternative explanation and the cheapest check that could
refute it. Prefer existing evidence and deterministic calculations before new runs.
Retire weak leads. A correct implementation is a useful conclusion when established.

Distinguish invalid successes from invalid failures. A pass may use an unintended
route; a failure may reflect scoring, environment, format or elicitation problems.
An auditor's inability to solve an item does not prove it unachievable. Check our own
parser, reconstructed tools, sandbox and configuration before blaming the benchmark.

Delegate item-level checks through inspect_audit/audit when useful. Select its items
(checks) and sample IDs for the question at hand; each auditor can inspect recorded
attempts and probe the benchmark scorer within operator scope. Pass relevant framework IDs, definitions, and the evidence question in worker notes; scope those notes to the worker's supplied assessment manifest rather than repeating the whole campaign population; item-level labels alone do not answer benchmark-wide checks. Treat verdicts as leads: verify the evidence
behind consequential claims and examine some unflagged cases before estimating
coverage. Retain your own benchmark-wide analysis; item checks cannot establish
population representativeness or cross-model comparability by themselves.

For an experiment, record the hypothesis, changed variable, fixed baseline and outcomes
that would change your conclusion. Reuse fixed candidate answers when comparing
scorers. Changing candidates, prompts and budgets together cannot isolate a cause.
Read running-jobs for submission, smoke testing, collection and spending controls.
Without remote capability, record the proposed experiment rather than claiming it ran.

## Evidence

Use source for code claims, transcripts for behaviour and actual scorer outputs for
scoring claims. Keep locators, relevant quotations and rerunnable calculations.
Summaries and worker verdicts do not substitute for primary evidence. After compaction
or a challenge, re-read the supporting record rather than trusting your journal.

State numerator, denominator and selection method. Purposive examples do not estimate
prevalence, regardless of sample count. Distinguish measured score effects from
hypothesised consequences. Do not subtract headline scores from incomparable runs.
Check dataset identity before joining by sample ID, especially across revisions.

The findings register follows report/findings.schema.json. Supported/qualified claims
need evidence; source inspection, historical observations, experiments and audit
limitations have separate origins. Use the journal for actions and corrections, and
the register for what is currently supported. Save scripts and tables under
report/evidence. Read writing before drafting; presentation rules live there.

## Focused references

Load eval-validity-review for a measurement checklist, investigate-dataset for dataset
inspection, and security-audit-eval when the benchmark executes untrusted code. Use
them for relevant questions; do not adopt their alternative report formats. The
writing skill and supplied report schema define publication.

## Comprehensive assessment

Use the framework's dimensions and checks as an investigation plan, not headings to
fill after item audits. Maintain report/assessments.json as work progresses. For each
outstanding check choose an informative next step or record a concrete scope/access
blocker. Specialist review can strengthen analysis; its absence is not a reason to skip
an assessment that source, papers, datasets and logs can support.

For content validity compare the claimed domain with actual task types, domain counts,
concentration, repetition and omissions. State the basis for any representativeness
judgment; counts alone do not establish a representative distribution.
For elicitation examine whether prompts, tools, agent loops, context, feedback, recovery
and limits give the model an appropriate opportunity to solve the task. Successful
execution and correct aggregation do not establish this. Choose targeted comparisons
where they can distinguish explanations; no fixed experiment is mandatory for every audit.

Review all unresolved assessment units, including those inside already-defective samples.
A complete label table is saved progress, not substantive completion. Reallocate available
worker funding toward useful follow-up rather than treating a worker cap as a final answer.
Before publication compare remaining investigations with their likely value and cost,
while preserving publication allowance. Do not spend merely to exhaust the budget.
