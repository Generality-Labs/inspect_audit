---
name: grading-labels
description: Record, for every question in the sample, whether its recorded grades are right. A submission that answers the question correctly but was graded incorrect, or an incorrect one graded correct, is a grading defect. This is phase 2's counterpart to question-labels and needs the logs.
metadata:
  grades: [NO_ISSUE_FOUND, DEFECT, UNRESOLVED]
  flags: [DEFECT]
  unresolved: [UNRESOLVED]
  tools: [grade]
  details:
    question_assessments: 'List of objects {question_id, status, checks, evidence, explanation, epoch_class, sub_reason, defect_types, scoring_effect, next_check, headline}, the same contract as question-labels. status is NO_ISSUE_FOUND, DEFECT, UNRESOLVED or NOT_ASSESSED and labels the recorded grades, not the item. Include each ID from assessment_ids in /audit/item.json exactly once. epoch_class and sub_reason are required for DEFECT: epoch_class is false_negative or false_positive, sub_reason one of that class''s list in this skill. defect_types, optional, from false_negative, false_positive, grader_failure. headline is required for DEFECT: one plain sentence, at most 25 words, saying what the grader got wrong, with no account of the audit. scoring_effect lists the misgraded submissions. grader_instability, optional, records one-off grader variance that is not a defect. sources, as in question-labels ({answer, source, url, quote, key}), lists the competing answers when a misgrade turns on what sources say, such as a rejected answer a credible source gives; empty otherwise.'
    misgraded: 'List of objects {question_id, log, sample, epoch, model, submission, recorded_grade, correct_grade, why}, one per recorded submission whose grade is wrong; empty when none is. log, sample and epoch locate the submission in /audit/logs exactly.'
---

# Classify the grades, not the question

The item's recorded attempts are under `/audit/logs`, one sample per run, each with
the submission the model made and the grade the benchmark's scorer gave it. The
question itself was labelled in phase 1, blind to these logs, and that label is
frozen: this audit judges whether each recorded grade is the right grade for the
submission it was given. The assessment units are listed under `assessment_ids` in
`/audit/item.json`.

The recorded grade is the claim under audit. For every recorded submission, read the
final answer, establish what the question asks and what the reference admits, and
decide what grade the submission deserves. Then compare.

- A **false negative** is a submission graded incorrect, or scored zero, that answers
  the question: the reference answer or an equivalent of it (an alias or alternative
  name, a unit conversion, formatting, precision within the stated tolerance, a
  reading the question's own wording admits).
- A **false positive** is a submission graded correct that does not answer the
  question: a different entity, a partial answer, a hedge across several candidates
  of which one is right, a reference matched by lenience rather than by meaning.
- A **grader failure** is a recorded grade that is not a judgment at all: the grader
  errored, timed out, returned nothing parseable, or graded a truncated submission,
  and does so again on re-grade or across submissions.

Each DEFECT names its class and the sub-reason that explains the misgrade:
`false_negative` with `incorrect_or_stale_key` (the key is wrong), `overly_strict_scorer`
(a valid alias, form, precision or granularity rejected), `systematic_grader_error`
(the grader misjudges for a reason the key does not explain, or fails as described
above), `live_state_drift` or `environment_failure`; or `false_positive` with
`lax_scorer` (a wrong, partial or hedged answer credited), `systematic_grader_error` or
`shortcut_or_leak`. `other`, in either, names the cause in the explanation.

Not defects: an abstention graded as the benchmark's rule says; a wrong answer graded
wrong; a submission that would be right only under a reading the question does not
support; a grade you merely disagree with on a judgment call the grading instructions
leave to the grader. An ambiguous question is phase 1's finding, not a grading error;
say in the explanation when you believe the item itself is defective, and label the
grading.

## Reproduce before you record

Use the grade tool to re-grade a submission you believe misgraded, with the current
grader. A grade that does not reproduce is grader variance. Only a systematic misgrade
is a defect: one that recurs across submissions, or that traces to the key, the rubric
or the grader instructions, such as a key that rejects a valid form of the answer. A
single submission misgraded, even one that reproduces on re-grade, or a grader that
errored once, is NO_ISSUE_FOUND: record it, with the repeated grades, in
`grader_instability` and the explanation as a limitation. A grader that rejects the
reference answer itself, submitted verbatim, is the strongest evidence of a false
negative and takes one call.

## Labels

NO_ISSUE_FOUND means every recorded submission's grade was checked and found right; it
is not proof the grader is perfect. List the actual checks. DEFECT requires a systematic
misgrade: at least one recorded submission whose grade is wrong for a reason the key,
rubric or grader instructions explain, cited by log, sample and epoch, with the
submission quoted and the grade it deserved. UNRESOLVED means you could not settle
whether a submission is equivalent to the reference, or could not read a recorded
grade. NOT_ASSESSED means no adequate assessment was undertaken. Never turn missing
work into NO_ISSUE_FOUND.

Every DEFECT carries a `headline`: one sentence, at most 25 words, that a reader with
the question open can check, naming what the grader got wrong. The register to match:

- The grader rejects "Ghulam Mohammad Shah" for the reference "Gul Shah".
- Answers in kilometres are marked wrong although the reference is in miles.
- A submission naming two candidates, one of them right, was graded correct.

When a misgrade turns on what sources say (a rejected answer that a credible source
gives, a key that excludes a published value), list the competing answers in the row's
`sources` as question-labels does: answer, source, public URL or exact citation, the
quoted passage, and `key: true` on the reference answer's own source.

`scoring_effect` lists the misgraded submissions; `misgraded` holds them as records,
one per submission. The auditor fills the page's Affected logs column from them. An
item's recorded submissions are few, one per run; read all of them, and when there are
more than a hundred say how you selected. Overall grade is DEFECT if any row has an
established defect; otherwise UNRESOLVED if any row is unresolved or unassessed;
otherwise NO_ISSUE_FOUND. Save the complete table through record_verdict after the
first bounded pass and update it as checks finish; do not leave saving until a
resource limit interrupts the agent.
