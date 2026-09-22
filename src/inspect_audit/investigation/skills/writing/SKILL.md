---
name: writing
description: Fill the pinned Generality Labs Audit Findings template with explained, quantified evidence; compile and inspect the PDF before publication.
---

## Contract

The deliverable is Laurence's Audit Findings section, using the supplied GL LaTeX
class and components unchanged. Fill report/metadata.tex and report/Findings.tex.
Keep all nine dimensionreview sections in A-I order and their dimensionoverview
commands. Do not substitute a thematic essay or HTML report. Do not add a cover,
authorship/approval fields, standalone scorecard, audit-setup section or rubric pages.
The framework and rubric are instructions for the auditor, not printed front matter.

Read framework/Framework.tex, framework/ScoringCriteria.tex and framework/checks.json.
The JSON includes each check's meaning, not just its name. Framework/examples contains
worked reports showing evidence blocks, figures and explanation depth. Their judgments,
benchmark facts and scope are not defaults for this investigation.

## Explain each dimension

Maintain assessments.json with every dimension and check exactly once. Assess the
question in its definition: a related observation is not a substitute. Explain ratings
through the framework's extent and consequence criteria. Overall grade, dimension
severity, defect prevalence and demonstrated score changes are distinct judgments.
The generated summary/check macros use these records; edit the records, not assessments.tex.

After the overview and check table, use the template's observedevidence and
concernevidence environments for substantive concerns. Explain what happened, how it
was established, what it changes about interpretation, and what remedy the evidence
supports. Cite primary evidence and concrete examples. Give each supported or qualified
finding a narrative home in its registered dimension with \label{finding:ID}; cross-reference
it elsewhere rather than repeating the entire explanation. Clean dimensions still explain
what was checked and the basis for that assessment. Scope exclusions are Not assessed,
not evidence that the benchmark passed.

Keep every material finding, including resource limits, in the relevant dimension.
Concision means removing repetition, generic caveats and administrative narration;
it does not mean dropping dimensions, evidence, or interpretation. Explain terms before
relying on them. Use plain British English and put conclusions before their support.

## Counts and figures

Use export_coverage from /inputs/coverage.py for coverage.json. Keep complete per-question
labels and explanations as linked supporting data. State the counted unit, denominator,
selection method, overlapping categories and demonstrated versus inferred effects where
numbers appear. A defective question is not automatically a misgraded submission.

Decide which findings benefit from visual explanation. Put diagrams and graphs inside
the relevant dimensions using boxfigure, with a takeaway caption, labelled axes, units,
denominators and a linked data table. Explain the evaluation pipeline where necessary;
show score changes and resource sensitivity when the evidence supports them. Do not
invent comparisons, intervals or a fixed quota of charts. Reference the figure in the
surrounding explanation; a graph is not a substitute for interpretation.

## Evidence and scope

Excluded investigation techniques do not create publication restrictions. Retain ordinary
scientific explanations and evidence unless an actual confidentiality constraint requires
redaction. If redaction is required, preserve an informative explanation and evidence
locator; do not replace every question's explanation with a generic placeholder.
Keep methodological details and costs in supporting artifacts, with brief relevant
limitations next to the claims they qualify. Never invent names, approvals or an overall grade.

## Publication

Call check_report to validate records and generate assessments.tex. Compile from
/workspace/report with latexmk -pdf -interaction=nonstopmode -halt-on-error report.tex.
Render every PDF page with pdftoppm -r 110 -png report.pdf preview/page (create preview first).
Inspect the resulting images with view_image. Check tables, page breaks, whitespace,
figure readability, typography and links; fix and recompile. A successful build or
text scan does not constitute visual review. If a renderer is missing, install it or
report the blocker rather than claim inspection happened.

Reconcile the supported findings register against the narrative and re-read each
assessment against its definition. Structural validation cannot establish semantic
correctness. Then call publish_report, which compiles and preserves the PDF, editable
LaTeX, figures and evidence as a versioned bundle.
