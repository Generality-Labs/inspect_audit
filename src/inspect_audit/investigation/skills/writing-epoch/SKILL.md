---
name: writing-epoch
description: Write the Epoch AI benchmark review for readers with a scientific background. Short sections with word limits, plain language, a verdict derived from the rubric rows. Read before recording any review row or narrative.
---

## Who reads this, and how

The review is read by people with a scientific background who do not work on model
evaluations. They want to know what the benchmark measures, whether its number can be
trusted, and what to keep in mind when they quote it. They will not read a long document.
Every section has a word limit and the limits are enforced when you publish.

Write plainly. Short sentences, one idea each. No paragraph longer than five sentences.
No jargon: say "the model that reads the answer out of the response" rather than
"the extractor", say "a reference answer" rather than "gold", and expand any acronym the
first time. Prefer numbers with their denominators over adjectives. Lead with the
conclusion, then the evidence. Do not narrate your own process, and keep execution
details, costs and pipeline errors out of the review; they belong in the journal.

## Contract

You maintain two records: `report/review.json` (schema in `report/review.schema.json`)
and `report/findings.json` (the evidence register). `check_report` validates them,
derives the verdict into `report/verdict.json` and renders `report/epoch_review.md`.
Edit the records, never the rendered markdown. `report/methodology.md` defines every
rubric row; read it before planning. Do not substitute any other report format.

The rubric rows decide the verdict. Fill them first, run `check_report` to learn the
derived verdict, then write the narrative that verdict calls for. Writing the narrative
before the verdict is known produces sections that will be rejected.

## The header

`benchmark` is the benchmark's name as a reader knows it ("Chess Puzzles", not a registry
path). `benchmark_creator` is the organisation that published it. `review_date` is the
publication date in ISO form and is filled in for you if left empty. The rendered header
reads: title, Benchmark, Benchmark creator, Verdict, Review date.

## The sections, by verdict

Every review has:

- **Summary** (at most 150 words). How the task works and what it tries to measure, what
  the best score is, and the key observations that decided the verdict. A reader who stops
  here should know the verdict and why.
- **Methodology** (at most 125 words). What you did: which logs and source you read, what
  you computed, which per-item audits ran and on how many items, what you did not do.

A **Verified** review adds, in this order:

- **Interpretation** (at most 300 words). What the benchmark measures, how its headline
  number is made (what is scored, how, and how results are aggregated), and how a reader
  should and should not interpret that number.
- **Task Analysis** (at most 300 words). How the tasks work, how they were created,
  whether they are reasonable, and how they are graded. Name any issue that matters to the
  score, with its count and denominator.
- **Elicitation and Scaffolding** (at most 300 words). The harness, prompting and tools
  the model is given; limits on tokens, turns or time; cost and resources where known;
  whether these settings let models show what they can do.
- **Limitations**. Bullet points only, each at most three sentences. The lead sentence is
  rendered for you ("While none of these limitations cross our threshold for a Flawed
  verdict, they do inform how a reader should characterize this benchmark."), so the
  bullets start straight on the point.
- **Recommendations**. Bullet points, each at most three sentences, addressed to the
  benchmark's creator: what to change, publish or document. One recommendation per
  limitation or error is the usual shape.

A **Flawed** review adds instead:

- **Representative Errors**. Bullet points, each at most three sentences, explaining the
  errors that crossed the threshold, followed by a table of concrete examples with
  columns Task, Error type, Notes, Affected logs. Task names the item or run; Error type
  is the defect class in a few words; Notes says what happened; Affected logs names the
  runs or models the error touches, with a count. Keep every cell under thirty words. Then Recommendations, as above. Do not write Interpretation, Task Analysis or
  Elicitation sections for a Flawed review; the rubric notes carry what the reader needs
  on those.

An **NEI** or **Incomplete** review adds Limitations, saying what could not be reviewed
and why.

## The rubric

The rendered rubric reproduces Epoch's form: every level, defect class and question is
printed with its fixed meaning, examples and threshold, every status option is listed and
the one you chose is shown in bold. You supply only the status, the numbers the form asks
for, and the notes. The Reviewability notes appear on the level you chose; the other
levels stay blank, as on the form. A disclaimer that the review used public information
without asking the creator closes the document; if that is not true of your review, say
so in Methodology.

Each rubric row carries a short note: at most eighty words and four sentences. State the
conclusion and the one or two facts that support it, with numbers and denominators. Do not
list evidence paths, scripts or file names in the notes; put the references in the row's
`evidence` list, which is rendered separately. A Pass or clean row says in one clause what
was checked.

## The scoring row and the 20% threshold

Prevalence must come from a sample selected for prevalence. Choose the questions for the
question-labels auditors with a recorded random seed, or label the whole population; a
purposive set can establish that a defect exists but not how common it is. The prevalence
is computed from `report/coverage.json`: questions labelled DEFECT over questions with a
resolved label, with unresolved and unassessed counts beside it. `check_report` refuses a
Pass or Flag that contradicts the computed threshold unless `threshold_override_reason`
says why. Without a coverage file, give `inspected` and `with_defect` on the row and say
how they were obtained. A question-level defect is not automatically a misgraded
submission, and an attack you authored against the current grader is not a historical
score error; say which kind each defect is.

## Benchmark-wide rows need benchmark-wide evidence

Consistency, elicitation and bias are not answered by item audits. Consistency compares
the scorer, instructions and reference answers across the logs' recorded versions and
grading models against what the leaderboard presents as one series. Elicitation compares
the recorded limits and settings with what the tasks need and with reasonable
alternatives. Bias looks for budgets or scaffolds that differ by model. Each needs its own
evidence references.

## Evidence

Every assessed row carries at least one evidence reference: a path under `/inputs` or in
the report bundle, a location within it, and a quotation where it helps. Two references
per row is usually enough; the register in `findings.json` carries the rest. `section`
on a finding names the rubric row it supports.

## Publication

Call `check_report`. Read the rendered markdown as the intended reader would, and fix
the records until it is short, plain and supported. Then call `publish_report`. Never
write the verdict yourself, never invent an overall grade, and never let the summary claim
more than the rows show.
