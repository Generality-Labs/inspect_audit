---
name: question-labels
description: Record an explicit validity assessment for every question or graded subproblem, with completed checks and evidence. This is not the evaluated model's pass/fail score.
metadata:
  grades: [NO_ISSUE_FOUND, DEFECT, UNRESOLVED]
  unevidenced: [UNRESOLVED]
  tools: [attempt, grade]
  details:
    question_assessments: 'List of objects {question_id, status, checks, evidence, explanation}. status is NO_ISSUE_FOUND, DEFECT, UNRESOLVED or NOT_ASSESSED. checks and evidence are arrays of nonempty strings. Include every graded subproblem in sample.metadata.sub_steps, excluding provided_code; otherwise the sample id. Preserve ids exactly.'
---

# Classify the question, not the model's answer

Examine the question, acceptance rule, source, and available attempts. Audit the
whole main problem together so earlier code dependencies remain visible. For
SciCode, return one row per graded subproblem, never the author-provided steps.
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
epoch and submission or command/output. Include intermediate feedback attempts and
scaling snapshots in the review inventory, not just final outputs. Bound detailed
inspection by the operator's scope and available budget; describe selection and
unreviewed evidence instead of claiming exhaustive transcript review. Resource and scaffolding differences
belong in the explanation, not silently in a pooled model ranking.

The parent needs a label for every question. After the first bounded review, save
the complete table, including unresolved/unassessed rows. Update it as more checks
finish; record_verdict replaces the previous table. Do not leave saving until a
hard resource limit interrupts the agent. Overall grade is DEFECT if any row has an established defect;
otherwise UNRESOLVED if any row is unresolved/unassessed; otherwise NO_ISSUE_FOUND.
Return the table through record_verdict details, so it survives sandbox cleanup.
