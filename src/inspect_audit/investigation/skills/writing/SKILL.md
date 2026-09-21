---
name: writing
description: Select consequential findings and present a concise, quantified audit using the Generality Labs framework. Read before drafting and publication.
---

## Executive summary

Include only findings that materially change how a reader should interpret or use the
benchmark: consequential specification or scoring defects, impaired elicitation, a
mismatch with its measurement claim, or a consequential positive result. A systemic
bug can qualify without many independent examples. Each point needs evidence and an
explanation of its consequence; dramatic wording is not evidence of significance.
State the finding, affected count where measured, and why it matters. Link its detailed
explanation. Group repeated mechanisms. Do not force a minimum number of bullets.
Audit completion, spending, package repairs, development work and configuration
differences are not findings by themselves. Put coverage in the results table, not
an extra summary bullet. Do not summarise everything the investigator spent time doing.

## Reading path

Use report/report.qmd: executive summary, computed results and scorecard, concise
benchmark description, substantive findings, then limitations of this analysis.
Laurence's framework in report/framework defines all nine dimensions and their checks;
retain its severity scale and examples. The HTML reading path need not reproduce the
LaTeX cover sheet or a separate audit-setup section. Keep setup and shared limitations
to roughly two sentences at the bottom; link detailed provenance, methods and costs.
No names, approvals or overall grades without evidence. Internal development and
merge instructions do not belong in the benchmark report.

Give each finding one full explanation: mechanism, evidence, affected extent and
consequence. Group repeated instances and cross-reference findings from other dimensions.
Keep complete check assessments in the generated assessment table; a clean dimension
does not require an essay. Do not confuse successful execution with adequate elicitation.

## Quantified results

Maintain report/assessments.json using report/assessments.schema.json and the check IDs
in report/framework/checks.json. Include one row for every dimension and every check.
Use None, Minor, Major, Critical, Not assessed or Not applicable. Explain ratings through
extent and consequence using the framework definitions, not numerical severity thresholds.
Findings use Minor/Major/Critical (or null while unassessed); dimension assessment
is a separate synthesis using the same framework definitions, not the maximum badge.
For sample-level checks supply checked_ids and affected_ids: e.g. X of Y questions omit
required information, split by mechanism. Link examples. For benchmark-wide checks where
question counts do not make sense, leave both lists null and give the measured result or
specific reason it could not be established. Never use an empty list to mean unknown.

For question audits write report/coverage.json using export_coverage from /inputs/coverage.py.
It retains disputed labels and missing work and computes overlapping defect-type counts.
Classify and verify recurring mechanisms across the assessed questions, rather than
reporting only selected examples. Questions with defects and submissions demonstrably
misgraded have different denominators. Selected examples do not establish prevalence.
Legacy unclassified defects remain visible; do not invent classifications to fill a table.
Publication checks structured records and generates report/audit-tables.html from them;
include it with the supplied Quarto include. These checks do not establish judgment accuracy.
Headline numbers must agree with these tables, not independently transcribed totals.

## Language and appearance

Use plain, concrete language and British English. Name what is counted: questions,
subproblems, submissions or runs analysed. Say what is wrong instead of using phrases
such as "evidenced validity defect" or "historical population". Call framework entries
checks, not registry contributions. State conclusions first, with support following.
Use the supplied labelled assessment badges consistently: None green, Minor amber,
Major orange, Critical red; Not assessed and Not applicable grey with distinct labels.
Colour supplements text. Keep affected counts and findings prominent alongside ratings.

A qualification belongs beside a claim only when it changes the interpretation. State
shared limitations once. "Scoring effect not measured" is preferable to a paragraph of
generic caution. Honest uncertainty stays visible; do not turn incomplete checks into
clean judgments. Compare scores only under conditions supporting that comparison.

Figures need a clear takeaway, labelled axes and units, explicit denominators and an
accessible source table. Do not draw intervals you did not compute. Preserve source
provenance for the supplied GL components. Avoid decorative charts that repeat a table
without making it easier to understand.

## Before publication

Review outstanding assessment units and framework checks before switching to writing.
Call check_report to validate records and generate the tables before rendering.
Then check every summary point against the significance criteria above, verify its
numbers and evidence, remove drafting notes and repeated caveats, render with Quarto,
and inspect figures. Publication validates structure and provenance, not truth or
editorial quality; read the rendered report before accepting it as finished.
