# Inspect Auditor roadmap, Q4 2026

Owner: Matt Fisher. Last revised 2026-09-28. This is the working plan for Outcome 3 of the GL Q4 plan: moving Inspect Evals maintenance onto Inspect Auditor. Prose reports, scorecards and the exhaustive audits (Outcomes 1 and 2) stay with James and Laurence; this doc covers only where their outputs meet ours.

Revise this doc when a milestone lands or a decision below is made. Implementation detail belongs in `docs/superpowers/specs/` and `docs/superpowers/plans/`, not here.

## Goal

Every eval in Inspect Evals has a current, hosted set of findings that a maintainer can act on and a user can consult before trusting a number. Contributors are pointed at those findings instead of at the issue tracker. Findings come from deterministic producers everywhere and from bounded agentic investigation on the Featured evals, with exhaustive audits on request.

## Principles

- Auditor uses inspect-evals-lint and inspect-dataset as producers. The codebases are not merged.
- Findings are records in one envelope schema with the producer's own output kept verbatim. See `docs/finding-schema-envelope.md`.
- The taxonomy is versioned data, not code. Old reports stay valid under `gl-audit@1`; new work targets `gl-audit@2`.
- The hosted index shows findings and dimension assessments. Grades are written by people and are never computed.
- Rollout is tiered by cost. SciCode-depth audits do not scale to 130 evals and do not need to.
- Audit outputs and agent working notes are private until published. Nothing under `agent_artefacts/` or `artefacts/` enters this repo.

## Where we are

Done on `findings-prototype` (PR #4 against `dev/integrated-audits`):

- Envelope schema, fingerprinting, parquet and run files.
- Adapters for inspect-evals-lint, inspect-dataset and `.eval` log headers.
- Versioned taxonomies `gl-audit@1` (the nine A to I dimensions) and a draft `gl-audit@2` (the strategy doc's seven), with a v1 to v2 mapping.
- Hawk access: `hawk:` log sources, `--hawk-task`, `hawk-sets`, and `hawk-pull` over `scripts/hawk-artefacts.yaml`.
- A deterministic pass over StereoSet and an acceptance sweep over six evals. The acceptance notes list what the aggregator needs first: a corpus filter on model and task args, grouping by rule, suppressions with reasons, dataset arguments taken from the eval's own `hf_dataset` call, and one-line skip messages.

Known from the acceptance sweep and worth a person's time now: strong_reject records 313 samples where the eval declares 324.

## Milestones, in order

Each milestone has a test that says when it is done. Dates are targets, not commitments.

### M1. Audit adapter over real investigation output (October, weeks 1 to 2)

Turn the investigator's registers (`findings.json`, `assessments.json`, `coverage.json`, question labels) into envelope findings. A check assessment becomes dimension, check and severity. A question label becomes a sample location with the defect type as rule. Evidence links become artifact locations.

Fixtures: the chess bundle already pulled by `hawk-pull`, and the SciCode registers once James hands them over. The SciCode investigation was not run on Hawk.

Done when: the SciCode v1.2 report's assessments and 288 unit labels round-trip into findings under `gl-audit@1` with no hand edits, and the chess bundle does the same.

### M2. Issues view for Inspect Evals maintainers (October, weeks 2 to 4)

A rendering over findings, not a new agent. Scope the first version to the Implementation dimension and its five contributions: task specification, environment, scaffolding, harness, grading. Group by defect type and check. Each candidate issue carries unit ids, source locators and evidence links, and reads like a GitHub issue a maintainer could file.

Also land the first aggregator items from the acceptance notes: corpus filter, grouping by rule, suppressions.

Done when: the SciCode findings render as a short list of candidate issues that Justin and Tania agree they would file, and the Featured-eval deterministic sweep renders without noise rows dominating.

### M3. Cost the middle tier (November, weeks 1 to 2)

Run a bounded investigation (source and logs, no commissioned experiments) on two or three Featured evals. Record cost, wall time and what it found against what the deterministic pass found.

Done when: we have a per-eval cost figure and a written tier definition. That figure decides the shape of M5.

### M4. Hosting with prioritisation (November)

A static site over the findings parquet: one page per eval, one index, a +1 on evals and on issues. Links to scorecards where one exists. No grades.

Open decision: GitHub-issue backlog of five to ten items with findings in a separate database (Justin's preference) versus GitHub Projects (Aleksey's). Write a one-page decision doc before building. The hosting choice should not depend on it, since both read the same findings.

Done when: the Featured evals are live at a URL linked from the Inspect Evals docs, and a +1 is recorded somewhere we can read.

### M5. Rollout across Inspect Evals (November to December)

Deterministic producers on every eval, on a schedule. Middle tier on the Featured evals at the cadence M3 says we can afford. Exhaustive audits stay on request.

Done when: every eval in the registry has a findings page less than a month old.

### M6. QA and documentation (December, continuous from M2)

Recall check against the gold-standard manual audits named in the strategy doc: FORTRESS, BixBench, SciCode. Fix what Auditor misses where the fix is deterministic. Update Auditor docs and the Inspect Evals contributing docs to point contributors at the findings.

Done when: recall against the three gold audits is measured and written down, and CONTRIBUTING.md in Inspect Evals links to the hosted findings.

## Decisions still open

- Land `dev/integrated-audits` on `main`, or keep working from it. Needs a conversation with James. Everything above is built on the dev branch.
- Backlog shape: GitHub issues plus findings database, or GitHub Projects. See M4.
- Whether the Inspect Evals view files issues automatically or only drafts them. Start with drafts.
- Whether `gl-audit@2` is final. Laurence's doc is a proposal; confirm the seven dimensions and the five Implementation contributions before M2 renders against them.

## Dependencies on other people

- James: the SciCode investigation directory (`work/report/` registers and `published/`), and the branch decision.
- Laurence: confirmation of the taxonomy v2 dimensions and contributions.
- Justin and Tania: review of the M2 issues view. They are its users.
- Hawk operators: nothing beyond `hawk login`. The 3.5.0 CLI extra omits `aiofiles`; the `remote` extra here adds it and an upstream issue should be raised.

## Adjacent work not in this plan

Matt's other Q4 items sit in Inspect Evals, not here: patching security problems and merging the HLE updates to main. The template adoption PR #5 fixes the red CI on this repo and lands independently of PR #4.
