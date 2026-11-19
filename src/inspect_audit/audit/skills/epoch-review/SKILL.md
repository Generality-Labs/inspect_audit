---
name: epoch-review
description: Conduct the audit as an Epoch AI benchmark review, rating the rubric's rows in auditor.ratings; the task audit alone without logs, the task audit then the log-dependent rows in the same run with logs. Read at the start of an audit whose publisher is epoch, after the auditing skill; where the two differ, this one applies.
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
was chosen, and take nothing else: `sample_design` is random, stratified, census or
purposive, with `drawn_by` when someone else drew it and `population` when the frame
differs from the dataset. The counts, the rate and the threshold are derived, never
written. A purposive sample shows that defects exist, not how common they are: the page
prints its counts without the threshold, and a passing row leaves the review Incomplete.

The quality rows with a form take their numbers in `fields`: contamination `as_of`,
`tasks_public_pct`, `solutions_public_pct`; score range `floor`, `ceiling`;
statistical adequacy `runs_per_model`.

## Two phases, one audit

Whether the audit was given evaluation logs (`supplied_logs` in the register's subject,
the `logs` sources the operator declared) decides how far it goes. It always begins
with phase 1 and, with logs, continues into phase 2 in the same run, before one
submission.

Phase 1 is the task audit, done without reading any log, even when logs were supplied.
It writes three rows and nothing else: reviewability from the source; scoring from the
sample labels; consistency from the source's version history, changelog and published
methodology. The question-labels investigators are sent with `logs=[]`, so each item is
judged as written, blind to how models fared on it. An audit with no logs stops here
and submits: every row the description marks `needs logs` stays unrated (submission
refuses it), a Flag ends the audit as Flawed with the page filled to that row, and a
pass leaves it Incomplete with the page saying the rest awaits the logs.

Phase 2 follows in an audit with logs, once phase 1's three rows are rated and none is
a Flag. It answers the remaining rows in rubric order from the headers and
transcripts, elicitation and bias first (the first Flag ends the rubric: the rows after
it stay unrated), then the quality rows, construct validity last with every other row in
hand. A grading-labels check over the same sample, sent with the supplied logs, labels
each item's recorded grades, and the defect rate is the union: an item defective as
written or as graded counts once.

| Stage | Evidence | Do | Rates |
|---|---|---|---|
| 0 Orient | source, docs, overview | the benchmark's facts; the population and its strata; the sample design | nothing yet |
| 1 Reviewability | source | can tasks, scoring logic and settings be inspected? | reviewability |
| 2 Items | the sample | question-labels on every sample: a two-item pilot, then the rest, then every item a worker left without a verdict re-run until resolved | scoring, with its sample design |
| 3 Consistency | source history, changelog, published methodology | did the scorer, instructions or references change without a version bump? | consistency |
| 4 No logs | the ratings | without logs, or on a Flag: submit, and publish a Flawed or Incomplete page | |
| 5 Grading (logs) | the logs | grading-labels over the same sample, `logs=` the supplied sources; the union decides scoring | scoring, revisited |
| 6 Benchmark-wide (logs) | headers, transcripts | elicitation, then bias; stop at the first Flag | those rows |
| 7 Quality (logs) | everything learned | the quality rows in order, construct validity last; then submit and publish | those rows |

## Rules for the item audit

- **Every sample ends resolved.** Unresolved and not assessed are not final labels
  for a review. A worker that stopped without a verdict, or returned UNRESOLVED, is
  sent again with the previous worker's notes and what remained open, as many times as
  the budget allows. Only after two workers still cannot settle an item may you decide
  it on the balance of their evidence, in a review that says so.
- **More than one defensible answer is a defect, when the second is strongly
  defensible.** A question is impossible as written when a reader with the sources
  could reasonably give a second answer the key rejects: credible sources genuinely
  conflict (a date, height or name that differs by source), or an unanchored
  convention makes the other reading reasonably likely, roughly one careful reader in
  three or more (which year a programme was "set up", whether a demo counts as an EP,
  the country then or now). Wording that admits another reading only remotely, where a
  careful reader would be about 70% or more confident of the intended one, is
  NO_ISSUE_FOUND with a note. Apply this the same way to a trivia key and to a
  function-calling trajectory. For example:
  - credible references publish different heights, or different names, for the same
    subject and the key takes one: DEFECT;
  - a question asks what something ran "using" and the energy-source reading is the
    evident one, though a device could be named: not a defect;
  - "before the renovation" where the context makes the period clear: not a defect.
- **A credible secondary source that conflicts with the key is enough.** To establish a
  second defensible answer, a reference work, a catalogue or an encyclopaedia citing a
  record suffices; a primary record is not required. A merely conceivable alternative
  reading is not enough. Say what would settle the item when sources conflict.
- **A one-off misgrade is not a defect.** A grading defect is systematic only if it
  recurs across submissions or traces to the key, the rubric or the grader
  instructions. A single submission misgraded, even one that reproduces on re-grade, is
  a limitation, not a defect: NO_ISSUE_FOUND with the misgrade noted.
- **A scorer that would credit a plainly wrong answer is a false positive**, whether or
  not a model has been seen to give one. Demonstrate it with a benign wrong answer
  through the real scorer; do not compose attacks on it.
- **Competing answers carry their sources.** The page prints, under the errors
  table, each defective item's competing answers, each with its source, its link and
  the quoted claim, from the investigators' `sources`. Where a DEFECT rests on
  conflicting sources or a second defensible answer and the standing worker recorded
  none, or you decide the item on other sources, give `sources` on your review:
  `[{answer, source, url, quote, key}]`, at least the key's answer (`key: true`) and the
  one it rejects, each with the public URL (never a local copy under /workspace or
  /tmp) or an exact citation, and the passage quoted. A review's sources replace the
  worker's for that check.
- **Your reviews, not the workers' labels, stand.** Where you disagree with a
  worker, record a review with the evidence that overturns it. Apply the bars above to
  every worker DEFECT, question or grading, and overturn one that does not meet them,
  saying which bar it fails. Do not relabel an item nobody finished: re-run it.
  Also send a second, adversarial worker, briefed to find the strongest credible
  competing answer, on NO_ISSUE_FOUND items where recorded models often gave the same
  wrong answer, or where the first worker's search for a competing answer was thin.
  Decide on both workers' evidence.

## Logs from another build

When the register's subject carries `logs_provenance`, the supplied logs were recorded
by a build of the benchmark other than the one installed here (Epoch's own package
against a reconstruction, or an older release). That is not a reason to stop: the
review looks for problems in those logs. It is a reason to say so, in the limitations
and wherever a reproduced grade disagrees with a recorded one, where the build
difference is the first suspect until the transcript shows the grader's reasoning.

## Evidence and the page

Every rated row carries evidence: a path under /inputs or /workspace and what was
observed there. The page's prose is written after submission, in the
epoch-publishing skill; the rubric notes are your explanations, so write them for the
page's reader from the start.
