---
name: question-labels
description: Record an explicit validity assessment for every question or graded subproblem, with completed checks and evidence. This is not the evaluated model's pass/fail score.
metadata:
  grades: [NO_ISSUE_FOUND, DEFECT, UNRESOLVED]
  unevidenced: [UNRESOLVED]
  tools: [attempt, grade]
  details:
    question_assessments: 'List of objects {question_id, status, checks, evidence, explanation, defect_types, scoring_effect, next_check}. status is NO_ISSUE_FOUND, DEFECT, UNRESOLVED or NOT_ASSESSED. checks and evidence are arrays of nonempty strings. Include each ID from /audit/assessment_ids.json exactly once. Preserve ids exactly. defect_types is a list of mechanism names, required and nonempty for DEFECT, empty otherwise. scoring_effect and next_check are optional strings.'
---

# Classify the question, not the model's answer

Examine the question, acceptance rule, source, and available attempts. Audit the
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
