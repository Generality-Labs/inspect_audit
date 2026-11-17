---
name: epoch-review
description: Conduct the audit as an Epoch AI benchmark review, in two phases, rating the rubric's rows in auditor.ratings. Read at the start of an audit whose publisher is epoch, after the auditing skill; where the two differ, this one applies.
---

The report is Epoch AI's benchmark review: a reviewability gate, four minimum-standard
checks each Pass, Flag or Not Reviewed (any Flag makes the benchmark Flawed), and
seven evaluation-quality checks that inform but do not disqualify. The description is
/workspace/analysis/epoch/methodology.md (/workspace/analysis/epoch/epoch.json as
data): the checks, their scales and guidance, the 20% scoring threshold and the
verdict rule. Read it before planning. The verdict is derived from your ratings at
submission; you never write it, and `ratings.overall` is refused.

## Ratings

Rate each check in `auditor.ratings` under its id, with its level as the label, a
short explanation (the row's note on the page: at most fifty words, three sentences,
no model names; "abstention handling is a material choice", not "GPT-5 mini abstained
on 33 questions"), and evidence. Pass is the default: Flag a minimum-standard check
only when the evidence shows a material effect on the headline results or their
comparability. A Flag on consistency, elicitation or bias carries `impact`, the
measured effect in at most three sentences with numbers. A check the audit did not
reach is left out, which rates it Not Reviewed.

The scoring row is decided by the sample labels, not by you: the defect rate is
computed at submission from the standing question-labels verdict on every sample (your
review where you wrote one, else the investigator's), DEFECT over DEFECT plus
NO_ISSUE_FOUND, against the threshold. Submission refuses a Pass at or above it and a
Flag below it unless the rating's `override` says why. Its `fields` say how the sample
was chosen: `sample_design` is random, stratified, census or purposive, with `drawn_by`
when someone else drew it; a purposive sample shows that defects exist, not how common
they are, and leaves a passing row Incomplete.

The quality rows with a form take their numbers in `fields`: contamination `as_of`,
`tasks_public_pct`, `solutions_public_pct`; score range `floor`, `ceiling`;
statistical adequacy `runs_per_model`.

## Two phases

Phase 1 is the task audit, with no evaluation logs. It writes three rows and nothing
else: reviewability from the source; scoring from the sample labels; consistency from
the source's version history, changelog and published methodology. Every row the
description marks `needs logs` stays unrated, and submission refuses it. A Flag ends
the audit as Flawed with the page filled to that row. A pass leaves it Incomplete: the
page says phase 2 awaits the logs.

Phase 2 is an audit with the logs declared. It answers the remaining rows in rubric
order from the headers and transcripts, elicitation and bias first (the first Flag ends
the rubric: the rows after it stay unrated), then the quality rows, construct validity
last with every other row in hand. With logs, a grading-labels check over the sample
labels each item's recorded grades, and the defect rate is the union: an item defective
as written or as graded counts once.

| Stage | Evidence | Do | Rates |
|---|---|---|---|
| 0 Orient | source, docs, overview | the benchmark's facts; the population and its strata; the sample design | nothing yet |
| 1 Reviewability | source | can tasks, scoring logic and settings be inspected? | reviewability |
| 2 Items | the sample | question-labels on every sample: a two-item pilot, then the rest, then every item a worker left without a verdict re-run until resolved | scoring, with its sample design |
| 3 Consistency | source history, changelog, published methodology | did the scorer, instructions or references change without a version bump? | consistency |
| 4 Submit | the ratings | without logs: submit, and publish a Flawed or Incomplete page | |
| 5 Grading (logs) | the logs | grading-labels over the sample; the union decides scoring | scoring, revisited |
| 6 Benchmark-wide (logs) | headers, transcripts | elicitation, then bias; stop at the first Flag | those rows |
| 7 Quality (logs) | everything learned | the quality rows in order, construct validity last | those rows |

## Rules for the item audit

- **Every sample ends resolved.** Unresolved and not assessed are not final labels
  for a review. A worker that stopped without a verdict, or returned UNRESOLVED, is
  sent again with the previous worker's notes and what remained open, as many times as
  the budget allows. Only after two workers still cannot settle an item may you decide
  it on the balance of their evidence, in a review that says so.
- **More than one defensible answer is a defect.** A question is impossible as written
  when a reader with the sources could reasonably give a second answer the key rejects:
  an unanchored convention (which year a programme was "set up", whether a demo counts
  as an EP, the country then or now), a date that differs by source. Apply this the
  same way to a trivia key and to a function-calling trajectory.
- **A credible secondary source is enough.** To establish a second defensible answer,
  a reference work, a catalogue or an encyclopaedia citing a record suffices; a primary
  record is not required. Say what would settle the item when sources conflict.
- **A scorer that would credit a plainly wrong answer is a false positive**, whether or
  not a model has been seen to give one. Demonstrate it with a benign wrong answer
  through the real scorer; do not compose attacks on it.
- **Your reviews, not the workers' labels, stand.** Where you disagree with a
  worker, record a review with the evidence that overturns it. Do not relabel an item
  nobody finished: re-run it.

## Evidence and the page

Every rated row carries evidence: a path under /inputs or /workspace and what was
observed there. The page's prose is written after submission, in the
epoch-publishing skill; the rubric notes are your explanations, so write them for the
page's reader from the start.
