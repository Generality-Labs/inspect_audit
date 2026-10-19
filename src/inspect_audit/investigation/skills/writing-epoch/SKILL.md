---
name: writing-epoch
description: Write the Epoch AI benchmark review for readers with a scientific mindset. Records against the methodology, short prose blocks with word limits, a verdict derived from the assessments. Read before recording any assessment or prose block.
---

## Who reads this, and how

The review is read by people with a scientific mindset who do not work on model
evaluations. They want to know what the benchmark measures, whether its number can be
trusted, and what to keep in mind when they quote it. So, write plainly. Short sentences, one idea each. No paragraph longer than five sentences. No jargon: e.g. say "the model that reads the answer out of the response" rather than "the extractor", and expand any acronym the
first time. Prefer numbers with their denominators over adjectives. Lead with the
conclusion, then the evidence. Do not narrate your own process, and keep execution
details, costs and pipeline errors out of the review; they belong in the journal. Keep analysis prose at a high-level, rather than going into the weeds.

## Contract

The methodology (`report/methodology.md`, with `report/methodology.yaml` as data) says
what to check and how to judge it; the template (`report/template.yaml`) says what the
page shows. You write five records in `report/`, each validated against the schema
beside it: `profile.json`, `assessments.json`, `sampling.json`, `findings.json` and
`prose.json`. In `profile.json`, `benchmark` is the common name used in literature and `creator` is the organisation that published it; `review_date` is filled in for you if left empty. The page is rendered from these
records by `check_report`: the verdict is derived from the assessments, the rubric
tables from their levels and notes, the commentary from `prose.json`. You never write
the page, and you never write the verdict.

## Order of work

The rubric is the plan, in two phases. Each stage ends with a record on disk and a
`check_report` run, so the verdict is always derivable from what has been written.
The rules of each stage are in your standing instructions, stated once there; these
tables are the map.

Phase 1, the task audit, reads no evaluation logs.

| Stage | Evidence | Do | Writes |
|---|---|---|---|
| 0 Orient | source, docs, overview | the benchmark's facts; the population and its strata; the sample design, yours or the operator's | `profile.json`, `sampling.json` |
| 1 Reviewability | source | can tasks, scoring logic and settings be inspected? | the reviewability row |
| 2 Items | the sample | question-labels and the substantive item checks, two items first, then the rest, then at most one repair batch for what the batch left open; no logs attached to any of them; the defect rate decides the scoring row | `coverage.json`, the scoring row |
| 3 Consistency | source history, changelog, published methodology | did the scorer, instructions or references change without a version bump? | the consistency row; the quality rows the documentation answers |
| 4 Publish | the derived verdict | the prose blocks it calls for; publish. Flawed on a Flag, otherwise Incomplete with a notice that phase 2 awaits logs | the page |

Phase 2, the effect analysis, is a resume of a published phase-1 audit with logs attached.

| Stage | Evidence | Do | Writes |
|---|---|---|---|
| 5 Effect | the logs | one gold-answer job over every recorded defect; what each defect did to each recorded result | Affected logs, footnotes |
| 6 Revisit | log headers | reviewability may rise; consistency re-checked against recorded versions and graders | those rows |
| 7 Benchmark-wide | headers, transcripts | elicitation and bias in rubric order; the first Flag ends the rubric, the rows after it are Not Reviewed | the rows up to and including the first Flag, with its `impact` |
| 8 Quality | no Flag in stage 7 | the log-dependent quality rows | assessments complete |
| 9 Publish | the derived verdict | the prose blocks it calls for; republish | the page |

Two consequences for the writing. Observations that would have gone into rows after a
Flag belong in the findings filed under the flagged check or in the journal, not in
rows the methodology says are not reviewed; `check_report` refuses a level written
there, and the page reads "[stop → Flawed]" where the rubric stopped. And the
supplied logs are the series the review compares against: a defect no logged
submission touches is still a defect for the measure, it simply has no effect to report.

## The blocks, by verdict

`report/methodology.md` ends with every prose block, its brief, its limit and the
verdicts it belongs to; write against that list. What it does not say. The lead
sentences of **limitations** and **representative_errors** are rendered for you, so
their bullets start straight on the point. **representative_errors** and the
**error_examples** table appear on every page, whatever the verdict, as soon as
`coverage.json` records a defect: one bullet per defect class with its row count and an
example or two, nothing about the audit, and one table row for every recorded defect,
naming its id in Task, then any run-level errors; the ids audited without a defect are
listed under the table for you. **recommendations** are addressed to the benchmark's
creator, one check or practice per class, one sentence each, naming no item; they are
printed after the disclaimer, so they close the page. A Flawed review has no
interpretation, task-analysis or elicitation block: the rubric notes carry what the
reader needs. **interpretation** is three short paragraphs: what the benchmark claims to
measure and what a score means; what the number is and is not; which findings bear on
that reading and how.

## Footnotes

Detail that supports a sentence but would clutter it goes in `footnotes`, a list in
`prose.json`, referred to as `[^n]` from any prose block or rubric note. Counts with
their denominators, which model did what at which setting, dates, versions and
locators belong there; the sentence on the page states the conclusion. A Flag's
`impact` is appended as a footnote for you. `check_report` refuses a marker with no
note and a note nothing refers to.

## The Notes cell

Each row's Notes is one sentence, at most 25 words, that says what is wrong with the
item. Start from the auditor's `headline` in `coverage.json` and tighten it;
the explanation and the numbers stay in the record and the findings. `check_report`
refuses a second sentence and the vocabulary of the audit (auditor, probe,
independent, established, unresolved, verified, candidate, attempt, agent). The
register to match, from Epoch's own reviews: "Asks for a 4-point DFT but the sequence
has 8 entries"; "Answer key gives the reciprocal of the answer found in its own
rationale"; "No answer option is fully correct". Not: "Explicit state-vector
evolution gives amplitude 1/2, hence probability 1/4, not target 0", which is the
finding's evidence, not its headline.

## Pass is the default

Flag a minimum-standard check only when the evidence shows a material effect on the
benchmark's headline results or on their comparability. A defect that exists but does not
move scores, or that affects only rows a reader can avoid, is a Pass with the concern
recorded under limitations. When the evidence leaves the question open, Pass and say what
would settle it. The methodology's guidance gives the test for each check. `impact`
exists only on a Flag; `check_report` refuses it on a Pass. A Flag on consistency,
elicitation or bias carries it as the measured effect in at most three sentences with
numbers, and the page prints it as a footnote under the row's note. On scoring, the
defect rate is the Flag; what the defects did to recorded results is phase 2's effect
job, run over every defect whatever the row's level, and reported in Affected logs and
the footnotes, with a scoring Flag's `impact` drawn from it; whatever it left unjudged
is unquantified, not a reason for another job.

## The rubric notes

The rendered rubric prints Epoch's form from the methodology, every option listed and
yours in bold; you supply only the level, the numbers the form asks for, and the
notes. A note is the conclusion in at most fifty words and three sentences, written
for the reader of the page: "abstention handling is a material choice", not "GPT-5
mini abstained on 33 questions". No model names, no run figures and no evidence paths
in a note; `check_report` refuses a model name, and the figures go in a footnote the
note refers to, with the paths in the row's `evidence` list. A Pass or clean row says
in one clause what was checked. On the scoring row the note is the defect rate against
the threshold in one sentence, with no repeat of the counts the prevalence line prints.
The disclaimer says the review used public information without asking the creator; if
that is not true of your review, say so in the methodology block.

## The scoring measure and the sample

The measure is computed from `report/coverage.json`: questions labelled DEFECT over
questions with a resolved label, unresolved and unassessed counts beside it. It is
admissible only from a sample selected for prevalence, recorded in `sampling.json`:
with logs supplied, the population is the items they contain and the sample is a
census of them or a random draw, one line to record; a draw made by someone else with a
stated random design is random, with the drawer named in `drawn_by`. A `purposive`
sample shows that defects exist, not how common they are, and leaves a passing scoring
row Incomplete. `check_report` refuses a Pass or Flag that contradicts the computed
threshold unless `threshold_override_reason` says why. A question-level defect is not
automatically a misgraded submission, and an attack you authored against the current
grader is not a historical score error; say which kind each defect is.

## Benchmark-wide checks need benchmark-wide evidence

Consistency, elicitation and bias are not answered by item audits. Consistency compares
the scorer, instructions and reference answers across the logs' recorded versions and
grading models against what the leaderboard presents as one series. Elicitation compares
the recorded limits and settings with what the tasks need and with reasonable
alternatives. Bias looks for budgets or scaffolds that differ by model. Each needs its own
evidence references.

## Evidence

Every assessed check carries at least one evidence reference: a path under `/inputs` or in
the report bundle, a location within it, and a quotation where it helps. Two references
per check is usually enough; the register in `findings.json` carries the rest.

## Publication

Call `check_report`. Read the rendered markdown as the intended reader would, and fix
the records until it is short, plain and supported. Then call `publish_report`. Never
write the verdict yourself, never invent an overall grade, and never let the summary claim
more than the assessments show.
