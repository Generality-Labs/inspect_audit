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

The rubric is the plan. Each stage ends with a record on disk and a `check_report` run,
so the verdict is always derivable from what has been written. The rules of each stage
(write as you go, scoring decided by the item audits alone, logs and attempts read only
after a scoring Pass, the first Flag ending the rubric and not the review) are in your
standing instructions, stated once there; this table is the map.

| Stage | Needs | Do | Writes |
|---|---|---|---|
| 0 Orient | source, docs, overview | the benchmark's facts; the population and its strata; the sample design, yours or the operator's | `profile.json`, `sampling.json` |
| 1 Reviewability | source, supplied log headers | can tasks, scoring logic and settings be inspected? | the reviewability row |
| 2 Items | the sample | question-labels and the substantive item checks, two items first, then the rest; the defect rate decides the scoring row, and a Flag there goes straight to stage 6 | `coverage.json`, the scoring row |
| 3 Logs | a scoring Pass; supplied logs, else the attempts job | read them; when they are on hand, the scoring row's impact is the submissions the defects misgraded; with neither logs nor eval models, say so in the methodology block | the attempts source |
| 4 Benchmark-wide | stage 3 | consistency, elicitation and bias in rubric order; the first Flag ends the rubric, the rows after it are Not Reviewed | the rows up to and including the first Flag, with its `impact` |
| 5 Quality | no Flag in stage 4 | the seven quality rows | assessments complete |
| 6 Publish | the derived verdict | the prose blocks that verdict calls for; publish | the page |

Two consequences for the writing. Observations that would have gone into rows after a
Flag belong in the findings filed under the flagged check or in the journal, not in
rows the methodology says are not reviewed; `check_report` refuses a level written
there, and the page reads "[stop → Flawed]" where the rubric stopped. And the
published results are the series the review compares against: the review's own
attempts measure what the confirmed defects do to real submissions, and a defect no
attempt touches is still a defect for the measure, it simply carries no impact figure.

## The blocks, by verdict

`report/methodology.md` ends with every prose block, its brief, its limit and the
verdicts it belongs to; write against that list. Three things it does not say. The
lead sentence of **limitations** is rendered for you, so its bullets start straight on
the point. **recommendations** are addressed to the benchmark's creator: what to
change, publish or document, if anything. A Flawed review has no interpretation, task-analysis or
elicitation block: the rubric notes carry what the reader needs, and the
**error_examples** table carries one row for every item `coverage.json` records as a
defect, naming its id in Task, then any run-level errors; the ids audited without a
defect are listed under the table for you.

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
would settle it. The methodology's guidance gives the test for each check. A Flag on consistency, elicitation or bias carries `impact`: the measured effect in at
most three sentences with numbers; `check_report` refuses one without it, and the
rendered Notes cell opens with it. On scoring, the defect rate is the Flag; give
`impact` only when attempts or supplied logs were on hand, as the submissions the
defects misgraded.

## The rubric notes

The rendered rubric prints Epoch's form from the methodology, every option listed and
yours in bold; you supply only the level, the numbers the form asks for, and the
notes. Each note is at most eighty words and four sentences: the conclusion and the
one or two facts that support it, with numbers and denominators. No evidence paths,
scripts or file names in the notes; those go in the row's `evidence` list, which is
rendered separately. A Pass or clean row says in one clause what was checked. The
page closes with a disclaimer that the review used public information without asking
the creator; if that is not true of your review, say so in the methodology block.

## The scoring measure and the sample

The measure is computed from `report/coverage.json`: questions labelled DEFECT over
questions with a resolved label, unresolved and unassessed counts beside it. It is
admissible only from a sample selected for prevalence, recorded in `sampling.json`. A
sample is random when its draw was random, whoever drew it: when the operator supplies
items an independent reviewer drew with a stated random design, record that design
and name the drawer in `drawn_by`, seed unknown if it is. Call a sample `purposive`
only when the items were chosen for a reason; its counts show that defects exist, not
how common they are, so a scoring row that passes on them leaves the review
Incomplete, though it can still be a Flag on a measured impact or carry a `measure`
obtained some other way. `check_report` refuses a Pass or Flag that contradicts the
computed threshold unless `threshold_override_reason` says why. A question-level
defect is not automatically a misgraded submission, and an attack you authored against
the current grader is not a historical score error; say which kind each defect is.

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
