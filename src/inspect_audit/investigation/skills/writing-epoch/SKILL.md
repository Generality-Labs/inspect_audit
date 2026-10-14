---
name: writing-epoch
description: Write the Epoch AI benchmark review for readers with a scientific background. Records against the methodology, short prose blocks with word limits, a verdict derived from the assessments. Read before recording any assessment or prose block.
---

## Who reads this, and how

The review is read by people with a scientific background who do not work on model
evaluations. They want to know what the benchmark measures, whether its number can be
trusted, and what to keep in mind when they quote it. They will not read a long document.
Every block has a word limit and the limits are enforced when you publish.

Write plainly. Short sentences, one idea each. No paragraph longer than five sentences.
No jargon: say "the model that reads the answer out of the response" rather than
"the extractor", say "a reference answer" rather than "gold", and expand any acronym the
first time. Prefer numbers with their denominators over adjectives. Lead with the
conclusion, then the evidence. Do not narrate your own process, and keep execution
details, costs and pipeline errors out of the review; they belong in the journal.

## Contract

The methodology (`report/methodology.md`, and `report/methodology.yaml` as data) says
what to check and how to judge it. The template (`report/template.yaml`) says what the
page shows. You write five records in `report/`, each with a schema beside it:

- `profile.json`: the facts about the benchmark every block draws on. `benchmark` is the
  name a reader knows ("Chess Puzzles", not a registry path); `creator` is the
  organisation that published it; `claim`, `snapshot`, `population`, `composition`,
  `scorer`, `grader`, `elicitation` and `harness` as you establish them. `review_date`
  is filled in for you if left empty.
- `assessments.json`: one row per check, in the methodology's order, each with `level`
  from that check's scale, `notes`, `evidence`, the `findings` ids it rests on, the
  `fields` the form asks for, and `impact` on a Flag.
- `sampling.json`: how the audited items were chosen: population, sample, strategy
  (census, random, stratified, purposive), strata, seed, expansion, confirmation.
- `findings.json`: the evidence register, each finding filed under the `check` it bears on.
- `prose.json`: the reader-facing blocks the template declares, keyed by block id.

`check_report` validates the records, derives the verdict into `report/verdict.json` and
renders `report/epoch_review.md`. Edit the records, never the rendered markdown. Do not
substitute any other report format.

The assessments decide the verdict. Fill them first, run `check_report` to learn the
derived verdict, then write the prose blocks that verdict calls for. Writing the prose
before the verdict is known produces blocks that will be rejected.

## Order of work

The rubric is the plan. Each stage ends with a record on disk and a `check_report` run,
so the verdict is always derivable from what has been written, and no stage reaches
for a tool it does not need yet.

| Stage | Needs | Do | Writes |
|---|---|---|---|
| 0 Orient | source, docs, overview | the benchmark's facts; the population and its strata; the sample design, yours or the operator's | `profile.json`, `sampling.json` |
| 1 Reviewability | source, supplied log headers | can tasks, scoring logic and settings be inspected? | the reviewability row |
| 2 Items | the sample | question-labels and the substantive item checks, two items first, then the rest | `coverage.json`, a provisional scoring row |
| 3 Attempts | supplied logs, else eval models | read the supplied logs now; with none, run the default eval on the sampled ids only, one epoch, inside `eval_budget_usd`; with neither, write that down | the attempts source |
| 4 Minimum standard | stages 2 and 3 | scoring impact measured on attempts where defects exist; consistency, elicitation and bias from headers, transcripts and the published methodology | the four rows, `impact` on any Flag |
| 5 Quality | all of the above | the seven quality rows | assessments complete |
| 6 Publish | the derived verdict | the prose blocks that verdict calls for; publish | the page |

A benchmark job before stage 2 is finished is premature: the items decide whether
attempts are needed and which ones matter. A benchmark job on the whole dataset is not
part of a review; the published results are the series the review compares against,
and the review's own attempts measure what the confirmed defects do to real
submissions. A defect no attempt touches is still a defect for the measure; it simply
carries no impact figure.

## The sample's provenance

A sample is random when its draw was random, whoever drew it. When the operator supplies
the items an independent reviewer drew with a stated random design, record that design
(`strategy`, `strata`) and name the drawer in `drawn_by`; the seed may be unknown. Call
a sample `purposive` only when the items were chosen for a reason rather than drawn.
A scoring row that passes on purposive counts leaves the review Incomplete: the counts
show defects exist, not how common they are. On a purposive sample the row can still
be a Flag when the impact of a confirmed defect is measured on real attempts, and it
can carry its own `measure` when you obtained one some other way.

## The blocks, by verdict

The template lists every block with its brief, its limit and the verdicts it belongs to;
`report/methodology.md` ends with the same list. In short:

- Every review: **summary** (150 words: how the task works and what it measures, the best
  score, the observations that decided the verdict) and **methodology** (125 words: which
  logs and source you read, what you computed, which per-item audits ran on how many items,
  what you did not do).
- **Verified** adds **interpretation**, **task_analysis** and **elicitation_and_scaffolding**
  (300 words each), **limitations** (bullets of at most three sentences; the lead sentence
  is rendered for you, so bullets start straight on the point) and **recommendations**
  (bullets addressed to the benchmark's creator: what to change, publish or document).
- **Flawed** adds instead **representative_errors** (bullets explaining the errors that
  crossed the threshold), **error_examples** (a table with columns Task, Error type, Notes,
  Affected logs; every cell under thirty words) and **recommendations**. No interpretation,
  task analysis or elicitation block: the rubric notes carry what the reader needs.
- **NEI** or **Incomplete** adds **limitations**, saying what could not be reviewed and why.

## Pass is the default

Flag a minimum-standard check only when the evidence shows a material effect on the
benchmark's headline results or on their comparability. A defect that exists but does not
move scores, or that affects only rows a reader can avoid, is a Pass with the concern
recorded under limitations. When the evidence leaves the question open, Pass and say what
would settle it. The methodology's guidance gives the test for each check; the ones that
decide most reviews are that an unversioned change needs a measured score effect to be a
Flag, and that under-elicitation is a Flag only when the affected models have no better
listed configuration. Every Flag carries `impact`: the measured effect in at most three
sentences with numbers. `check_report` refuses a Flag without it, and the rendered Notes
cell opens with it.

## The rubric

The rendered rubric reproduces Epoch's form from the methodology: every level, defect
class and question is printed with its fixed meaning, examples and threshold, every
status option is listed and the one you chose is shown in bold. You supply only the level,
the numbers the form asks for, and the notes. The Reviewability notes appear on the level
you chose; the other levels stay blank, as on the form. A disclaimer that the review used
public information without asking the creator closes the document; if that is not true
of your review, say so in the methodology block.

Each assessment carries a short note: at most eighty words and four sentences. State the
conclusion and the one or two facts that support it, with numbers and denominators. Do not
list evidence paths, scripts or file names in the notes; put the references in the row's
`evidence` list, which is rendered separately. A Pass or clean row says in one clause what
was checked.

## The scoring check and the 20% threshold

The measure must come from a sample selected for prevalence. Choose the questions for the
question-labels auditors with a recorded random seed, or label the whole population, and
record the design in `sampling.json`; a purposive sample can establish that a defect
exists but not how common it is, and its coverage counts are not used as the measure. The
measure is computed from `report/coverage.json`: questions labelled DEFECT over questions
with a resolved label, with unresolved and unassessed counts beside it. `check_report`
refuses a Pass or Flag that contradicts the computed threshold unless
`threshold_override_reason` says why. Without a coverage file, give `measure` (numerator
and denominator) on the assessment and say how they were obtained. A question-level
defect is not automatically a misgraded submission, and an attack you authored against the
current grader is not a historical score error; say which kind each defect is.

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
