# Findings and publishing

An investigation has two phases in the same Inspect agent conversation. Before submission it sees investigation skills and job tools. Publishing skills and tools are introduced only after `submit_findings` succeeds. Bash, calculations, images and the existing conversation remain available; job launch and collection tools are removed. Host submission code also refuses remembered experiment calls.

## Draft

The investigator edits `/workspace/findings.json` with Bash or Python. Its JSON schema is `/workspace/findings.schema.json`.

- `subject` identifies the benchmark and task arguments.
- `auditors.checks` snapshots each check's grades and their meanings.
- `auditors.samples` contains every selected sample/check and its original worker verdicts, including explanations, observations, model, run, eval and epoch.
- `investigator.summary` states what the investigation established.
- `investigator.findings` contains `criterion` (framework ID such as C or C.1), `issue`, `explanation`, `sample_ids` and `evidence`. An evidence observation has `observed` and `source`.
- `investigator.reviews` contains `sample_id`, `check`, `label`, `explanation` and `evidence`. Reviews accompany the original auditor verdicts.
- `investigator.ratings` holds broader dimension judgments, each with a label, explanation and supporting evidence. Use framework dimension or check IDs as keys. The default LaTeX publisher also requires `ratings.overall`, with an A-D label, explanation and evidence, before submission.
- `investigator.limitations` records gaps and blockers.
- `investigator.artifacts` lists absolute `/workspace/...` or `/inputs/...` paths needed by a fresh publisher: tables, calculation scripts and evidence extracts.

The analytical registry is generated from the pinned upstream framework; no TeX template is packaged. It is staged under `/workspace/analysis/framework`; its definitions are reference material. Ratings live only in `findings.json`, with no separate `assessments.json` to maintain. Publication tables and graphs are derived from this submitted data.

`start_register` establishes the population. Collection refreshes the draft's subject and auditor section without replacing the investigator's edits. The host retains the collection register; it is not a second publication format. Submission refuses edited auditor records or references to samples outside the population.

Empty cells and unresolved verdicts, including `NO_VERDICT`, remain visible. Progress queries and publication use the same interpretation of each sample/check. They do not prevent submitting a scoped result, but publication records missing or unresolved planned checks as incomplete. An explicit `required_coverage` continues to require submitted coverage at `/workspace/analysis/coverage.json`; include that file in artifacts.

## Submission

Finish or stop child jobs, then collect them. A terminal job without logs records a collection failure rather than disappearing. `submit_findings` validates the draft and saves a trusted snapshot outside the agent's writable workspace. There is no reopening or replacement submission in this first version.

A bundle contains:

```text
findings.json
manifest.json
evidence/inputs/...
evidence/workspace/...
```

The manifest records file checksums, artifact mappings, collected jobs and native log references. Native logs stay at their original addresses with checksums. They are not copied into Hawk report artifacts, where `.eval` files can be ingested as new logs. Include the extracts and derived tables needed to understand the findings without access to the original conversation or raw logs.

Inspect checkpoints the submitted data, manifest and phase. A fresh host restores and checks evidence from the checkpointed sandbox; editing the sandbox copy cannot replace the submitted claims. A publisher may revise presentation repeatedly while using the same frozen bundle.

## Default LaTeX publisher

`latex` is the default for investigation and standalone publication. It fetches Laurence's template from `Generality-Labs/audit_reports` at revision `ad7de89658b6794112c2f65847991c7ed41d9e30`, with SHA-256 for every class, style, asset, build configuration and Example source recorded in `publishers/template.json`. Files are checked on every use in the host cache (`INSPECT_AUDIT_TEMPLATE_CACHE`, default `~/.cache/inspect-audit/templates`). There is no vendored template.

The repository is private. Prefill the cache with `python -m inspect_audit.publishers._template`, using host-side `gh auth` or a read-only `GH_TOKEN`. The token is never passed to the agent sandbox. CI can use a read-only `AUDIT_REPORTS_TOKEN` secret in the `audit-template` environment; its PDF integration check reports a skip if no token/cache is supplied.

Install `latexmk`, `texlive-latex-extra`, `texlive-fonts-recommended`, `texlive-fonts-extra` (Carlito) and `texlive-science` on the publishing host, or build the separate image:

```sh
docker build -f src/inspect_audit/containers/publishing.Dockerfile -t inspect-audit:publishing src/inspect_audit/containers
export INSPECT_AUDIT_LATEX_IMAGE=inspect-audit:publishing
```

The agent writes `/workspace/report/prose.json` with `datasheet`, `setup` and `dimensions` (A-I) prose. Code escapes the text and generates the cover, contents, scorecard, datasheet, setup, nine dimension assessments and contribution rows, finding boxes and scoring criteria. Grades, sample counts and evidence references come from frozen findings, never the prose file. Contribution findings receive A.n labels; broader dimension findings receive F-n labels. Unknown metadata and missing assessments are displayed explicitly. Compilation runs on the host or publishing image, with networking disabled in the image. Output preserves the PDF, generated TeX, prose, template provenance and original findings bundle.

The default standalone entry point is `inspect eval inspect_audit/publish -T bundle=/path/to/findings.json --model openrouter/openai/gpt-6-luna`. Choose `publisher=gl` explicitly for the optional web publisher described below, or use an installed `module:factory` plugin.

## Publish again from a saved bundle

For the optional GL web publisher, install the browser dependency on the machine running Inspect, not in the agent's sandbox:

```sh
uv sync --extra publishing
uv run python -m playwright install --with-deps chromium
uv run inspect eval inspect_audit/publish \
  -T bundle=/path/to/findings.json -T publisher=gl \
  --model openrouter/anthropic/claude-haiku-4-5
```

The adjacent manifest and evidence are required. A bundle directory is also accepted. No benchmark package, original investigator conversation or job credentials are required. The supplied model still needs its normal inference credentials.

## Default GL publisher

The publishing skill asks the agent to write `/workspace/report/report.md`. It can calculate from existing evidence but cannot launch new benchmark experiments.

`figure` registers a predefined `bars`, `stacked-bars` or `histogram` component. Its inputs are JSON data, caption, axis label and bundle-relative source references. Use `{{figure:NAME}}` on its own line in Markdown. Agent-written drawing code and Markdown image embeds are not accepted by this publisher.

`preview_report` runs the reviewed GL website components in headless Chromium and returns HTML, PDF and a preview image to the sandbox. The agent can inspect the image and revise its presentation. `publish_document` builds an immutable output bundle with static HTML, vector PDF, SVG figures, Markdown, figure input data and a copy of the submitted findings. Each publication records its findings checksum.

The GL component source is packaged at a pinned website revision. D3 and Observable Plot distribution assets are version-pinned and downloaded on first use in each publishing workspace. The host needs network access for those assets. Browser requests during report rendering are restricted to the supplied page and assets.

## Other publishing packages

Set `publisher=module:factory` at launch. The trusted installed factory returns:

```python
from inspect_audit import Publisher

# build(root, bundle) is async and returns a host directory of finished output.
# root is the run directory; bundle is the trusted submitted findings directory.
def factory():
    return Publisher(skills=["/path/to/publishing"], build=build, tools=tools)
```

Skills follow Inspect's skill format. Optional `tools(root)` returns native Inspect tools. The package builds its own presentation; the common publication tool adds and preserves the submitted findings bundle. Package selection is fixed by the investigation's launch configuration. A standalone publish run can choose a new package for the same bundle.
