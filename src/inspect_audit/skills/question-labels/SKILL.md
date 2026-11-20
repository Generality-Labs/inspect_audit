---
name: question-labels
description: Record an explicit validity assessment for every question or graded subproblem, with completed checks and evidence. This is not the evaluated model's pass/fail score.
metadata:
  grades: [NO_ISSUE_FOUND, DEFECT, UNRESOLVED]
  flags: [DEFECT]
  unresolved: [UNRESOLVED]
  tools: [grade]
  details:
    question_assessments: 'List of objects {question_id, status, checks, evidence, explanation, epoch_class, sub_reason, defect_types, scoring_effect, next_check, sources}. status is NO_ISSUE_FOUND, DEFECT, UNRESOLVED or NOT_ASSESSED. checks and evidence are arrays of nonempty strings. Include each ID from assessment_ids in /audit/item.json exactly once. Preserve ids exactly. epoch_class and sub_reason are required for DEFECT and absent otherwise: epoch_class is impossible_as_written, false_negative or false_positive, and sub_reason one of that class''s list in this skill. defect_types, optional, is a list of mechanism names from the list in this skill, empty unless DEFECT. scoring_effect, next_check and grader_instability are optional strings. sources lists the competing answers a DEFECT rests on, as objects {answer, source, url, quote, key}: every competing answer, the key''s included (key: true), each with the work and its publisher, the public URL (or an exact citation for print), and the passage quoted; required whenever the defect is conflicting sources or a second defensible answer, at least two different answers, empty otherwise.'
---

# Classify the question, not the model's answer

Examine the question, acceptance rule, source, and available attempts. Investigate the
sample together so dependencies remain visible. The assessment units are listed
under `assessment_ids` in /audit/item.json; their meaning comes from the operator notes and
benchmark evidence, not a built-in dataset convention.
Consult the other selected skills to audit task specification, grading and
failure attribution. Reproduce consequential claims where the tools permit it.

NO_ISSUE_FOUND means specified checks completed without finding a validity defect;
it is not proof the question is flawless. List the actual checks, and record in
checks and evidence the competing answers you looked for, the queries you ran and the
pages you opened. A clean label without that search is NOT_ASSESSED. DEFECT requires
evidence for a concrete problem with the question or acceptance rule. An unexpected
model failure, disagreement, or timeout alone does not establish a defect.
A second answer makes a DEFECT only when it is strongly defensible: credible sources
genuinely conflict with the key (one credible secondary source that conflicts is
enough), or the other reading is reasonably likely, roughly 30% or more of careful
readers. A merely conceivable reading is not: where a careful reader would be about
70% or more confident of the intended reading, label NO_ISSUE_FOUND and note the
ambiguity in the explanation. Credible references giving different heights or names
for the same subject is a DEFECT; asking what something ran "using" when the
energy-source reading is evident, or "before the renovation" when context fixes the
period, is not.
A recorded model objecting to the question, or hedging around it, is a claim to
check against the source, not evidence of a defect. Establish the fact yourself:
count, compute, quote the passage. A grade of your own authored answer is evidence
only when repeated; a model grader can flip on the same text.
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

## Class, sub-reason and follow-up

Give every DEFECT one of Epoch's classes and one sub-reason from that class, naming
the root cause:

- `impossible_as_written`: a careful solver cannot reliably reach the keyed answer.
  `ambiguous_wording` (the wording admits two readings), `conflicting_sources`
  (credible sources disagree on the fact), `multiple_valid_answers` (several answers
  fit and the key takes one), `missing_information` (a period, unit or scope the answer
  depends on is not given), `hidden_requirement` (the key needs something the question
  never asks), `false_premise`.
- `false_negative`: a correct answer would be scored wrong. `incorrect_or_stale_key`,
  `overly_strict_scorer` (a valid alias, full name, unit or granularity rejected),
  `systematic_grader_error`, `live_state_drift` (the answer depends on live state that
  changes), `environment_failure` (a sandbox or harness failure independent of the
  agent).
- `false_positive`: a wrong answer would be scored right. `lax_scorer`,
  `systematic_grader_error`, `shortcut_or_leak` (the answer is reachable without the
  skill, from the harness or a leak).

`other` is allowed in every class, with the cause named in the explanation. A wrong
key is a false negative even when one source repeats it; sources that disagree on the
fact itself make the item impossible as written.

Optionally, `defect_types` also names mechanisms: `missing_information`,
`specification_test_contradiction`, `incorrect_reference_or_test`,
`insufficient_test_coverage`, `hidden_requirement`, `surface_form_sensitivity`,
`grader_context_omission`, `environment_failure`, `harness_failure`, or `other`.
Explain the actual missing requirement or contradiction, not just its category.

## Competing answers carry their sources

When a DEFECT rests on conflicting sources or on a second defensible answer, record
every competing answer in the row's `sources`, the key's answer included, so a reader
can check each one:

```json
{"answer": "Jimmy McHugh", "source": "International Swimming Hall of Fame, honoree page",
 "url": "https://ishof.org/...", "quote": "With Sava's Crystal Plunge team she worked with Jimmy McHugh", "key": false}
```

`source` names the work and who published it; `url` is the public address you read it
at (for a page you saved, the address you fetched, never the local copy), or an exact
citation (volume, page, document number) for print; `quote` is the passage that gives
the answer, a sentence or two, copied, not paraphrased. Mark the reference answer's
own source with `"key": true`. A defect with no competing answer (a missing file, a
broken harness) has no sources.

A grader that scores the same answer differently on repeat grading is not a defect in
the question. Label the question on its own merits and put the instability, with the
repeated grades, in `grader_instability`.
In scoring_effect distinguish a demonstrated effect on a recorded submission from
an unmeasured consequence. A bad test does not prove every failed answer was correct.
For unresolved work, name the next useful check or the specific blocker in next_check.
Early labels preserve progress; continue auditing unresolved units even if another
unit already makes the sample's overall verdict DEFECT. Update labels as evidence improves.
