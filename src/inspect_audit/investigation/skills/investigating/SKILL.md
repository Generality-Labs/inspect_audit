---
name: investigating
description: Investigate an Inspect repository and supplied logs, preserve evidence, and publish a Quarto report for interactive follow-up.
---

Read /inputs/seed.json. For a local repository, unpack /inputs/source.tar into
/workspace/source (a committed snapshot, with no Git history or submodule contents).
For HTTPS inputs, clone the seed's repo into /workspace/source and check out the
specified revision using Git. Record the resolved commit in /workspace/source-revision.txt.
Read the paper if supplied; pdftotext is installed. Missing access is a limitation.

Orient from the paper, task, dataset, solver, scorer and setup code. Inspect headers
and summaries of every supplied log before selecting detailed transcripts. Use
Inspect's log APIs; preserve the distinction between benchmark runs and auditor runs.
No source execution or model reruns are needed just to start reading evidence.

Follow promising leads, including population-level failures. Save calculations and
their populations. Maintain /workspace/journal.md and /workspace/report/findings.json.
Each finding records its current status, evidence locations, reproduction procedure,
limitations and corrections. A single reproducible defect can establish a finding;
do not manufacture a prevalence estimate from a selected example.

## Report

Edit /workspace/report/report.qmd. Follow its SimpleQA-derived structure: an opening assessment, The benchmark,
How models respond, Issues (finding-specific subsections), and Bottom line.
Remove drafting comments. Read report/findings.json when writing and revising;
do not maintain a second claims list. Every substantive finding needs an evidence reference. Store the
single findings register in report/findings.json using the schema in
report/findings.schema.json. Each entry has id, claim, status, origin, evidence,
reproduce and limitations. Evidence entries contain path, location and an optional
quote. Paths must be report-relative (e.g. evidence/counts.csv) or /inputs paths.
Supported and qualified claims need evidence. File existence is checked at publication;
quoted text and interpretations still need independent verification. Copy retained calculations, tables and relevant excerpts
into report/evidence so the published bundle can be examined independently.
Do not copy irrelevant or sensitive inputs into the report merely to make it larger.

Use report/components.py for bar charts and transcript blocks; its functions explain
their arguments. Run Python scripts from the report directory so imports and relative
paths resolve. Other figure shapes may use matplotlib directly with the same restrained
style. Chart titles belong in the report; label axes and units, and include denominators.
Avoid redundant subtitles, decorative text and captions repeating the title.

Use a concise overview, ranked findings, quantitative results where justified,
limitations and next steps. Each finding explains the observation, measurement
consequence and evidence. Separate observed effects from proposed explanations.
Do not invent findings or fill sections with generic benchmark criticism.

Call publish_report to render and save a version outside your writable workspace.
In interactive mode, then wait for questions. Revisions must keep earlier publications
intact. Report incomplete coverage honestly; passing the render check is not verification.
