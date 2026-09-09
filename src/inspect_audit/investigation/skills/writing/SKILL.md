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

Follow the template's sections and headings exactly. Each finding in a section gives
its mechanism in a sentence, the evidence with a locator, the affected population as a
count over a denominator, the consequence for the score (or "unmeasured"), and what
would change the conclusion.

A figure gets a one-line title that states the takeaway, labelled axes with units, and
nothing else on the canvas: no annotations, no stats boxes, no subtitle explaining the
method. Series are named plainly and directly labelled where there are few enough. The
caption carries the denominator. If a number matters, it goes in the prose, not on the
plot. Prefer raw values to smoothed ones. Never draw an interval you did not compute.

## What every report has to state

Somewhere the reader can find it, and once only: what you ran and what you read. How
many samples out of how many, on what dates, with what model versions and package
revisions, the total tokens and the cost. If you ran jobs yourself, they are in the
ledger and the collected logs; take the numbers from there, not from memory.

Where a published number exists for this benchmark, put yours next to it: the reported
figure, the figure you measured, the difference, and where the reference came from. An
unexplained difference is worth stating as a difference. Do not explain it away.

Say the size of what you tested. A hypothesis checked on eight samples is reported as
eight samples. Anything under roughly twenty cases, or fewer than three cases per
subgroup you are comparing, is examples rather than a rate, and the sentence says so.

## Two paragraphs

Bad: "I carefully reviewed the grading configuration and found that there may be some
differences between the paper and the implementation which could potentially affect the
comparability of the results, although the magnitude of this effect is not assessed."

Good: "The logs were graded by a different judge from the paper's: every one of the 49
records `model-a` as grader, the paper-faithful config specifies `model-b`. The scores
are therefore not paper reproductions. How much the judge choice moves scores is
unmeasured here; a paired regrade of one model's 1,000 attempts would measure it."

## Before publishing

Read the rendered report with render_report. Remove every drafting comment. Check each
number against the table it came from. Look at each figure with view_image. Then
publish.
