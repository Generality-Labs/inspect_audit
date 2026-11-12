---
name: latex-publishing
description: Publish frozen findings using Laurence's pinned GL audit template, with generated scorecard rows and finding boxes.
---

Findings are final at /inputs/findings/findings.json. Read the manifest and evidence.
Write /workspace/report/prose.json with these optional prose fields:

```json
{"datasheet": "Benchmark context and architecture.", "setup": "Audit methods and scope.", "dimensions": {"A": "Additional explanation of the recorded construct-validity assessment."}}
```

Only these fields are accepted. They contain plain text, not LaTeX. Dimension keys
are A-I. Grades, assessments, contributions, finding boxes, sample counts and evidence
references are generated from the submitted findings; they cannot be changed here.
Missing metadata is shown as not recorded. Do not invent approvals or measurements.

Call preview_report to compile, inspect report.pdf (rasterise its pages with pdftoppm
if available), and revise prose as needed. Call publish_document to preserve the PDF,
generated sources, template provenance and original findings bundle. TeX runs on the
publishing host or its separate publishing image, never in this audit box.
