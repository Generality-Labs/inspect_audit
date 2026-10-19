---
name: grading-labels
description: Record, for every question in the sample, whether its recorded grades are right. A submission that answers the question correctly but was graded incorrect, or an incorrect one graded correct, is a grading defect. This is phase 2's counterpart to question-labels and needs the logs.
metadata:
  grades: [NO_ISSUE_FOUND, DEFECT, UNRESOLVED]
  unevidenced: [UNRESOLVED]
  tools: [grade]
  details:
    question_assessments: 'List of objects {question_id, status, checks, evidence, explanation, defect_types, scoring_effect, next_check, headline}, the same contract as question-labels. status is NO_ISSUE_FOUND, DEFECT, UNRESOLVED or NOT_ASSESSED and labels the recorded grades, not the item. Include each ID from /audit/assessment_ids.json exactly once. defect_types is required and nonempty for DEFECT, from false_negative, false_positive, grader_failure. headline is required for DEFECT: one plain sentence, at most 25 words, saying what the grader got wrong, with no account of the audit. scoring_effect lists the misgraded submissions.'
    misgraded: 'List of objects {question_id, log, sample, epoch, model, submission, recorded_grade, correct_grade, why}, one per recorded submission whose grade is wrong; empty when none is. log, sample and epoch locate the submission in /audit/logs exactly.'
---

# Classify the grades, not the question

The item's recorded attempts are under `/audit/logs`, one sample per run, each with
the submission the model made and the grade the benchmark's scorer gave it. The
question itself was labelled in phase 1, blind to these logs, and that label is
frozen: this audit judges whether each recorded grade is the right grade for the
submission it was given. The assessment units are listed in
`/audit/assessment_ids.json`.

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
  errored, timed out, returned nothing parseable, or graded a truncated submission.

Not defects: an abstention graded as the benchmark's rule says; a wrong answer graded
wrong; a submission that would be right only under a reading the question does not
support; a grade you merely disagree with on a judgment call the grading instructions
leave to the grader. An ambiguous question is phase 1's finding, not a grading error;
say in the explanation when you believe the item itself is defective, and label the
grading.

## Reproduce before you record

Use the grade tool to re-grade a submission you believe misgraded, with the current
grader. A grade that reproduces is systematic; one that does not is grader variance.
Both are defects when the recorded result is wrong, since the recorded result is what
the leaderboard shows; say which it is in the explanation. A grader that rejects the
reference answer itself, submitted verbatim, is the strongest evidence of a false
negative and takes one call.

## Labels

NO_ISSUE_FOUND means every recorded submission's grade was checked and found right; it
is not proof the grader is perfect. List the actual checks. DEFECT requires at least
one recorded submission whose grade is wrong, cited by log, sample and epoch, with the
submission quoted and the grade it deserved. UNRESOLVED means you could not settle
whether a submission is equivalent to the reference, or could not read a recorded
grade. NOT_ASSESSED means no adequate assessment was undertaken. Never turn missing
work into NO_ISSUE_FOUND.

Every DEFECT carries a `headline`: one sentence, at most 25 words, that a reader with
the question open can check, naming what the grader got wrong. The register to match:

- The grader rejects "Ghulam Mohammad Shah" for the reference "Gul Shah".
- Answers in kilometres are marked wrong although the reference is in miles.
- A submission naming two candidates, one of them right, was graded correct.

`scoring_effect` lists the misgraded submissions; `misgraded` holds them as records,
one per submission. The parent fills the page's Affected logs column from them. An
item's recorded submissions are few, one per run; read all of them, and when there are
more than a hundred say how you selected. Overall grade is DEFECT if any row has an
established defect; otherwise UNRESOLVED if any row is unresolved or unassessed;
otherwise NO_ISSUE_FOUND. Save the complete table through record_verdict after the
first bounded pass and update it as checks finish; do not leave saving until a
resource limit interrupts the agent.
