---
name: epoch-publishing
description: Publish frozen findings as an Epoch AI benchmark review page. The verdict, the defect rate, the rubric tables and the list of audited items are generated from the findings; you write the representative errors (bullets and table), within their limits.
---

Findings are final at /inputs/findings/findings.json. Read them with their manifest,
and read /workspace/analysis/epoch/methodology.md, which ends with every prose block,
its brief and its limits. Write /workspace/report/prose.json:

```json
{
  "profile": {"benchmark": "SimpleQA Verified", "creator": "Google", "review_date": "2026-11-17"},
  "representative_errors": ["..."],
  "error_examples": [{"task": "117", "error_type": "...", "notes": "...", "affected_logs": "..."}],
  "footnotes": ["..."]
}
```

The page is the header, the rubric, then the representative errors: your bullets, the
errors table, grouped by Epoch's class (impossible as written, false negative, false
positive) with each group's count and each row's sub-reason taken from the findings,
and, under it, each defective item's competing answers with their sources, printed
from the findings; any notice (no logs, logs from another build) follows, and
the page ends there. Do not write URLs into the table; a source missing from the page
is missing from the findings.

Write nothing else. The summary, methodology, limitations, recommendations and the
other blocks methodology.md lists as "not rendered" are not on the page and not
required; one written anyway is checked against its limits and kept in prose.json,
but spend the effort on the rubric notes and the errors instead.

`profile.benchmark` is the benchmark's common name and `creator` the organisation that
published it; `review_date` is today's date when left out. The `representative_errors` bullets and the
`error_examples` table are required on every page as soon as the sample records a
defect, with one table row per defect naming its sample id in `task`; the items audited
without a defect are listed under the table for you. Write each row from that item's
own records (its Affected logs from its grading-labels `scoring_effect` and
`misgraded`), and revise a row by its `task`, never by its position in the list: an edit
by index lands in the neighbouring row. `preview_review` points out rows whose cells
share a run of words.

## Who reads this, and how

People with a scientific mindset who do not work on model evaluations. Short
sentences, one idea each; no paragraph longer than five sentences; no jargon ("the
model that reads the answer out of the response", not "the extractor"); numbers with
their denominators, not adjectives; the conclusion first, then the evidence. Do not
narrate the audit: costs, pipeline errors and what you tried belong in the journal.
`preview_review` refuses a plain cell or bullet that speaks of the audit (auditor,
probe, verified, unresolved) rather than of the benchmark.

## Footnotes

Detail that supports a sentence but would clutter it goes in `footnotes`, referred to
as `[^n]` from a representative-errors bullet or a rubric row's explanation: counts
with their denominators, which model did what at which setting, dates, versions,
locators. A Flag's `impact` is appended as a footnote for you. A note nothing refers
to, or a reference to a note that is not written, is refused; the page prints only the
notes it refers to. Leave `footnotes` out when nothing needs one.

## Publish

Call `preview_review`: it renders /workspace/report/epoch_review.md and reports the
derived verdict with its reasons. Read the page as its reader would and revise
prose.json until it is short, plain and supported; the findings cannot change. Then
`publish_document`, which keeps the page, the derived verdict, the sample coverage, the
rubric rows, the prose and the description the page was rendered against beside the
findings bundle.
