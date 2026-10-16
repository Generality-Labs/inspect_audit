---
name: question-labels
description: Record an explicit validity assessment for every question or graded subproblem, with completed checks and evidence. This is not the evaluated model's pass/fail score.
metadata:
  grades: [NO_ISSUE_FOUND, DEFECT, UNRESOLVED]
  unevidenced: [UNRESOLVED]
  tools: [attempt, grade]
  details:
    question_assessments: 'List of objects {question_id, status, checks, evidence, explanation, defect_types, scoring_effect, next_check}. status is NO_ISSUE_FOUND, DEFECT, UNRESOLVED or NOT_ASSESSED. checks and evidence are arrays of nonempty strings. Include each ID from /audit/assessment_ids.json exactly once. Preserve ids exactly. defect_types is a list of mechanism names, required and nonempty for DEFECT, empty otherwise. headline is required for DEFECT: one plain sentence, at most 25 words, saying what is wrong with the item for a reader who has it open, with no account of the audit. scoring_effect and next_check are optional strings.'
---

# Classify the question, not the model's answer

Examine the question, acceptance rule and source, and any attempts you were given. Audit the
sample together so dependencies remain visible. The assessment units are listed
in /audit/assessment_ids.json; their meaning comes from the operator notes and
benchmark evidence, not a built-in dataset convention.
Consult the other selected skills to investigate task specification, grading and
failure attribution. Reproduce consequential claims where the tools permit it.

NO_ISSUE_FOUND means specified checks completed without finding a validity defect;
it is not proof the question is flawless. List the actual checks. DEFECT requires
evidence for a concrete problem with the question or acceptance rule. An unexpected
model failure, disagreement, or timeout alone does not establish a defect.
UNRESOLVED means attempted checks could not settle validity. NOT_ASSESSED means no
adequate assessment was undertaken. Never turn missing work into NO_ISSUE_FOUND.

No attempts or historical logs are needed to settle validity: this check comes before
any model is run, and most samples arrive without logs. Judge the item on its text, its
key and its grader, and test the acceptance rule with the grade tool, submitting an
ordinary correct answer and, when it helps, a wrong one. An otherwise supported label is
never UNRESOLVED because logs are absent; UNRESOLVED is for a substantive question the
checks could not settle.

Keep historical grading accuracy separate: an observed format exception does not
prove a numerically correct candidate was rejected. Cite the exact log, sample,
epoch and submission or command/output. Include relevant intermediate work in the review inventory, not just final outputs. Bound detailed
inspection by the operator's scope and available budget; describe selection and
unreviewed evidence instead of claiming exhaustive transcript review. Resource and scaffolding differences
belong in the explanation, not silently in a pooled model ranking.

The parent needs a label for every question. After the first bounded review, save
the complete table, including unresolved/unassessed rows. Update it as more checks
finish; record_verdict replaces the previous table. Do not leave saving until a
hard resource limit interrupts the agent. Overall grade is DEFECT if any row has an established defect;
otherwise UNRESOLVED if any row is unresolved/unassessed; otherwise NO_ISSUE_FOUND.
Return the table through record_verdict details, so it survives sandbox cleanup.

## The headline

Every DEFECT carries a `headline` beside its explanation: one sentence, at most
25 words, that a reader with the question in front of them can check against it.
It names what is wrong with the question or its key, not how you found out. No
method, no tool names, no hedges, no account of checks; a number only when the
number is the defect. The explanation holds the evidence; the headline is what the
page prints. The register to match:

- Asks for density as a function of height, but the answer key is a single number.
- The author's own rationale matches option E, but the key is D.
- The key's time-dilation factor has a misplaced decimal (0.9963 vs 0.963).
- Multiple molecules satisfy every listed constraint, but only one is in the key.
- The case already states the confirmed diagnosis, then asks for the next diagnostic step.

Not this: "Independent normal-B continuity and tangential-H jump equations give
option E; key D has the wrong sign in its interior denominator." That is the
explanation, and it belongs there.

## Defect mechanisms and follow-up

Classify established defects by cause. Reuse these names when applicable:
`missing_information`, `specification_test_contradiction`, `incorrect_reference_or_test`,
`insufficient_test_coverage`, `environment_failure`, `harness_failure`.
Use another concise mechanism name when these do not fit; multiple types may apply.
Explain the actual missing requirement or contradiction, not just its category.
In scoring_effect distinguish a demonstrated effect on a recorded submission from
an unmeasured consequence. A bad test does not prove every failed answer was correct.
For unresolved work, name the next useful check or the specific blocker in next_check.
Early labels preserve progress; continue investigating unresolved units even if another
unit already makes the sample's overall verdict DEFECT. Update labels as evidence improves.
