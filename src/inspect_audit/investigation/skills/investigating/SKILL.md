---
name: investigating
description: Investigate an Inspect benchmark's task, grader, harness, aggregation and agent behaviour from its source and logs; preserve evidence; publish a six-section Quarto report.
---

## Setup

Read /inputs/seed.json. Unpack /inputs/source.tar into /workspace/source: it holds only
the paths named in the seed (the task under audit, not every eval in the repository).
For an HTTPS repo, clone into /workspace/source at the seed's revision and record the
resolved commit in /workspace/source-revision.txt. The paper, when supplied, is already
under /inputs/paper (pdftotext is installed); a URL left in the seed means the download
failed, fetch it yourself with curl.

Read the documentation before using the tools it documents. /inputs/docs holds the
Inspect documentation (and Hawk's, when supplied); the log-reading and log-analysis
skills summarise the API. Reading them first is faster than guessing at it.

Use curl for anything on the web. Verify externally sourced claims against primary
artefacts before repeating them. Commands longer than a few minutes: run them in the
background with nohup, write output to a file under /workspace, and poll; the shell
call itself times out at 300 seconds.

## Investigation

Orient from the paper, task, dataset, solver, scorer and setup code. Inventory every
supplied log by header and sample summaries before opening transcripts. Preserve the
distinction between benchmark runs and any auditor runs. Follow the strongest leads.
Save every calculation with its population as a script and a table under /workspace.

Maintain /workspace/journal.md (append-only: what you did, what you found, what you
corrected) and /workspace/report/findings.json (the register of what is supported now).
A finding has: id, section (task | grader | harness_environment | aggregation_limits |
agent_behaviour | construction), claim, status (hypothesis | supported | qualified |
retracted), origin (historical | experiment | source | audit_limitation), evidence
(path, location, optional quote), reproduce, limitations. Paths are report-relative
(evidence/counts.csv) or /inputs paths. Supported and qualified findings need evidence.
A single reproducible defect can establish a finding; do not manufacture a prevalence
estimate from a selected example. Keep reusable scripts under /workspace/report/evidence
with the tables they produce; consolidate scratch work rather than accumulating it.

## Report

Edit /workspace/report/report.qmd. Its structure is fixed: a three-paragraph summary,
then The task, The grader, The harness and environment, Aggregation and limits, How
agents approach it, How it is built. Each section answers its questions in prose with
numbers that carry denominators and sources; say "not assessed" with a reason where
you could not check something. Findings in the register appear under their section.
Remove the drafting comments.

Figures come from report/components.py (bar_chart, stacked_bars, line_chart,
table_from_csv, transcript); run scripts from the report directory. One short title
per figure in the document, labelled axes with units, plain series names, no
annotation dumps. Denominators in captions. Copy the tables and excerpts the report
relies on into report/evidence so the published bundle stands alone.

Call render_report to read the rendered draft and fix what is wrong; look at figures
with view_image. Then call publish_report, which validates the register and saves an
immutable version. Rendering is not verification. In interactive mode, then wait for
the operator; follow-up work can publish another version.
