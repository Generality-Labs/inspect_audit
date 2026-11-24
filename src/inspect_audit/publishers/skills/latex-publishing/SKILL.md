---
name: latex-publishing
description: Write and publish the GL audit report from the frozen findings, in Laurence's template.
---

A person will read this report. They need to decide whether to trust this benchmark's scores, they have not seen the audit, and their time is limited. Write for them, not for a checker.

The findings are final at /inputs/findings/findings.json. The GL rubric is in the audit-framework skill: use its criteria to explain each rating in plain words, not to repeat its codes.

## Writing

- Open each section with its main finding in one sentence. No preamble.
- No jargon. Explain every term in plain English the first time you use it. Never put variable names, job ids, criterion codes or file paths in the prose. Call each thing by one name throughout.
- Be dense. Every sentence should tell the reader something they did not already have. Cut restatement, filler and hedging. Keep sentences short and avoid chains of clauses joined by commas.
- Use the number that changes the conclusion, with its denominator, and say what happened to the remainder ("77 of 112 attempts did X; the other 35 did Y").
- Calibrate. Write "shows" only when the evidence shows it; otherwise "suggests". A one-off grader slip or an unlikely reading is a note, not a defect. Put the findings that most change what the scores mean first.
- For each finding, say what happens, how you established it, what it does to the scores, and what should change.
- Use literal language: no metaphors, no dramatic or promotional words, no em-dashes.

## Evidence

- Paraphrase evidence in your own words. When one piece of evidence carries a finding (a few lines of a transcript, or a test input with its expected and actual output), insert it with the excerpt component instead of describing it. Use excerpts judiciously: at most one per finding, the shortest that proves the point, followed by one sentence on what it shows.
- The report prints no paths, log ids or hashes, and neither should the prose. Where each piece of evidence came from is kept with the published findings.

## Prose and excerpts

Write /workspace/report/prose.json. Only these fields are accepted, as plain text (not LaTeX); a blank line starts a paragraph:

- `datasheet`: what the benchmark is and asks, in the Eval Datasheet.
- `setup`: what this audit did and did not cover, under Scope of Report.
- `dimensions`: `{"A": "...", ..., "I": "..."}`, one entry per dimension, printed after that dimension's ratings table and before its evidence and findings. A criterion's text (C.1) belongs under its letter.

Grades, ratings, finding boxes, counts and evidence come from the frozen findings and cannot be changed here.

An excerpt is declared in `excerpts` and placed in any of those fields with `{{excerpt:<id>}}` on its own line. Declare where the text is; the publisher reads it, so never type the quoted text yourself:

```json
{"id": "replay", "caption": "GPT-5, bibliography converter in Go, attempt 1",
 "source": "/inputs/logs/20/run.eval", "sample": "bib2json_go", "epoch": 1, "message": "FKN3ZHh2j9d6PxM4az5BG2",
 "start": "// Try precomputed mapping", "end": "// Fallback"}
```

- `source`: a file the findings cite (/workspace/... as in the manifest's `artifacts`), or a log in the manifest's `log_references` (its `source` or `address`) with `sample`, `epoch` and `message` (a message id, or its 0-based position).
- Choose lines with `lines: [first, last]` (1-based), or `start` and optionally `end`: text copied exactly from the first and last lines to show. Without either, the whole text is shown.
- `max_lines` (default 12, at most 40): further lines are cut and the box says how many.
- `kind`: `text` for prose (a prompt, a model's message, a document or paper), set as a block quotation in the report's font; `code` for recorded output (a program, a command, a tool's output, test results), set line for line in a monospace box. Unset, a tool call or tool output, or a file with a code extension, is `code`, and anything else is `text`. Set it only when that guess is wrong.
- Nothing is redacted unless you ask. `redact` (optional) lists strings to replace with "[redacted]", each of which must occur in the selected lines. Use it only for a deliberate reason, never by default.
- `caption`: a plain label naming whose words or which document it is (model, item, attempt; or the document and section), with no description of what it shows. It is the link text: an excerpt from a Hawk log links to that sample in the Hawk log viewer. Say what the excerpt shows in the sentence after it.

preview_report fails, naming the excerpt, if a source, sample, message, line or start/end text cannot be found, or an excerpt is unplaced or placed twice. It writes the excerpts exactly as quoted, with where each was read and its link, to /workspace/report/excerpts.txt.

## Rendering

- Call preview_report and look at every page as the reader will. Before publishing, fix blank pages, clipped tables and any section that reads as a wall of text. If something cannot be fixed from the prose, say so in the report.
- Before publishing, check that no number, name, quote or verdict differs from the findings.
- Then call publish_document. It preserves the PDF, its sources, the template provenance and the findings bundle.
