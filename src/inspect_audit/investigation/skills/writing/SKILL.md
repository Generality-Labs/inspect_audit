---
name: writing
description: How to write the audit report so a reader who knows the domain but did not watch you work gets the findings, their size and their evidence with no wasted words. Read before drafting report.qmd.
---

## Who reads it and why

The reader is deciding whether to trust a number. They know what a benchmark is. They
did not watch you work and do not care how you worked, except in the short method note.
They want to know what you found, how big it is, how you know, and what would change
your mind. Give them that, in that order, in every section.

## Sentences

Lead with the finding and its size. The first sentence of a section or paragraph is
the conclusion; support follows. One idea per sentence, plain words, an active verb.
Around twenty words; split anything that needs a semicolon. No em dashes; use a
comma or a full stop. British English.

Numbers carry their denominators and their scope in the same sentence or the next one:
"812 of 1,000 attempts" not "81%" alone; "among scored attempts" when that is the
population. Three or more numbers go in a table, not a sentence.

Do not narrate your process in the body. "I reviewed", "I inspected", "the audit
examined" belong in the method note or nowhere. State what is the case and cite where
it is shown. Do not pad claims with qualifications that name no alternative: a
qualification is worth writing only when it says what would change the conclusion.
Say "not assessed" and why, in one sentence, when you did not check something.

## Structure and figures

The report follows the Generality Labs audit framework: headline numbers and scorecard,
eval datasheet, audit setup, and findings under all nine assessment dimensions.
Use the supplied report skeleton and framework source, not a self-invented structure.
The exact template is report/framework/Content.tex; the contribution registry and
assessment definitions are in report/framework/auditframework.sty and ScoringCriteria.tex.
The vendored GL components are in report/assets/gl.js, with provenance beside them.
For each dimension state None, Minor, Major, Critical, Not assessed, or Not applicable.
No issue found requires completed checks; absence of a finding is not validation.
Question validity and grading accuracy have different denominators and must be separate.
Use question-labels scores to build the coverage table with export_coverage from /inputs/coverage.py;
keep NOT_ASSESSED and UNRESOLVED visible. Disagreements require review, not majority voting.
GL visual components may display these computed tables, with an accessible static table.
Do not fill approval/verification names or assign an overall grade without evidence. The summary introduces the benchmark in
2–3 sentences, then gives one linked bullet per substantive finding, positive or
negative. Each bullet leads with the conclusion and evidence or magnitude, with its
scope. Finish with a short coverage statement. Put execution failures and detailed
methods in the full audit; mention them briefly above only when they limit findings.

Every detailed finding explains the mechanism, evidence locator, affected population,
score consequence (or unmeasured consequence), and limitations. Add explicit anchors
so the brief links directly to the relevant finding. Group repeated instances.

A figure gets a one-line title that states the takeaway, labelled axes with units, and
nothing else on the canvas: no annotations, no stats boxes, no subtitle explaining the
method. Series are named plainly and directly labelled where there are few enough. The
caption carries the denominator. If a number matters, it goes in the prose, not on the
plot. Prefer raw values to smoothed ones. Never draw an interval you did not compute.
Save every figure as PNG.

## What every report has to state

Somewhere the reader can find it, and once only: what you ran and what you read. How
many samples out of how many, on what dates, with what model versions and package
revisions, the total tokens and the cost. If you ran jobs yourself, they are in the
ledger and the collected logs; take the numbers from there, not from memory.

Compare scores numerically only when the conditions support an interpretable
comparison. Otherwise state which candidate, judge, dataset or budget differs without
subtracting unrelated headline scores.

Say the size of what you tested. A hypothesis checked on eight samples is reported as
eight samples. Anything under roughly twenty cases, or fewer than three cases per
subgroup you are comparing, is examples rather than a rate, and the sentence says so.

## Before publishing

Render the report with quarto and read it back. Remove every drafting comment. Check each
number against the table it came from. Look at each figure with view_image. Then
publish.
