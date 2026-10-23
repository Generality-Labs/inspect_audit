---
name: publishing
description: Publish final findings with predefined GL components as HTML and PDF.
---

Read /inputs/findings/findings.json and manifest.json. The findings are final. Do not change them or run new benchmark experiments. Use the manifest's artifact mapping to find evidence carried from the audit. Native logs are external references with checksums; use included extracts and calculated tables for a portable report. No fact needed for publication should depend solely on the previous conversation.

Write /workspace/report/report.md in Markdown. Explain the conclusions and their support, scope, denominators and limitations. Preserve disagreements and missing work. You can organise the narrative and calculate summaries from existing evidence. Never turn a calculation into a claim that a new experiment was run. There is no reopening workflow: if a submitted finding appears mistaken, state that limitation explicitly.

For figures, calculate JSON data with Bash/Python, then call figure. Available types:

- bars: rows {"label":"...", "value":1, "group":"..."}.
- stacked-bars: rows {"label":"...", "segments":[{"name":"...", "value":1, "tone":"bad"}]}.
- histogram: rows {"value":1, "series":"..."}.

Name the source files from the findings manifest. Supply a caption with units and an explicit denominator. A figure's input table and GL component revision are saved. Place {{figure:NAME}} on its own line in report.md. Drawing code belongs to the pinned GL component library, not agent-written plotting scripts. Markdown image embeds and raw HTML are not part of this publisher. Add graph types through the package interface.

Call preview_report, then view_image on /workspace/report/preview.png. Review axes, legends, figure readability and prose before publishing. Revise the report and figure inputs as needed; the findings remain unchanged. Call publish_document to preserve static HTML, vector PDF, SVG figures, Markdown and figure data with the findings bundle.
