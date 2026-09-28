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
- Inspect Auditor is an installable Inspect package with registered tasks whose behaviour lives in skills. It has no GUI and should not grow one. The hosted site is a separate static thing that reads the findings.
- One owner for the finding schema. James (v1 report), Laurence (summary MVP) and Matt (issues view) are all touching report format. Matt owns the schema; the report formats read from it.
- A finding carries its evidence locators and can be re-run before and after a fix. Individual verdicts flipped about one time in ten on test-retest in James's logs, so aggregates are the signal, and nothing closes a contributor issue without a human looking.
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

## The pipeline, cheapest first

All stages write the same finding schema.

1. Deterministic pass, no model spend: inspect-evals-lint over source, inspect-dataset over the HuggingFace dataset, header checks over any logs we hold. Built.
2. Sample audits over existing logs. Most Inspect Evals evals have no logs we hold. The Featured evals with evaluation reports do, which is why they are the pilot set. Where logs are missing the investigator has to commission a Hawk run first, and that is where cost lands.
3. One bounded investigation per eval producing the registers. The expensive step; M3 prices it.
4. Aggregate and host. One dataset across every register, a static page per eval, a +1 backed by a small Worker and a D1 table copied from the Cloudflare worker template and the telemetry worker.
5. Feedback loop. Votes feed prioritisation. Defects Auditor missed are raised on this repo with logs, per the Q4 plan's contributor process.

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

### M2b. Recall against known Inspect Evals defects (October, week 4)

We already hold a labelled set of real defects: the 40 scoring PRs reviewed on 2026-09-09 and the 125-PR triage from 2026-09-15. Run the deterministic pass, and the M2 view, over the evals those PRs touch and count how many of the defects appear as findings. Recall against that set is the hill-climb metric for the Inspect Evals flavour, the way Epoch's reviews are for the prose reports. Build the measurement before scaling to 130 evals, not after.

Done when: recall is a number in a committed note, with the misses listed as candidate producer rules.

### M3. Cost the middle tier (November, weeks 1 to 2)

Run a bounded investigation (source and logs, no commissioned experiments) on two or three Featured evals. Record cost, wall time and what it found against what the deterministic pass found.

Done when: we have a per-eval cost figure and a written tier definition. That figure decides the shape of M5.

### M4. Hosting with prioritisation (November)

A static site over the findings parquet: one page per eval, one index, a +1 on evals and on issues. Links to scorecards where one exists. No grades.

Open decision: GitHub-issue backlog of five to ten items with findings in a separate database (Justin's preference) versus GitHub Projects with a scored board (Aleksey's). Write a one-page decision doc before building. The current lean is Justin's split, because a thousand generated findings would drown a project board and Auditor can regenerate them. Either way the +1 and the promote-to-issue step must be designed so Aleksey's maintenance agent can consume them.

Before the site goes public, re-read the security review that led to the frontend being deleted from the dev branch. Full rollout means Auditor runs arbitrary benchmark containers at scale, and the hosted surface is the part outsiders touch.

The Q4 plan also wants links from a pop-up when users run an eval and from the eval logs. Both need Inspect Evals code changes that are not ours; they become requests to Tania or Aleksey once the URL exists.

Done when: the Featured evals are live at a URL linked from the Inspect Evals docs, and a +1 is recorded somewhere we can read.

### M5. Rollout across Inspect Evals (November to December)

Deterministic producers on every eval, on a schedule. Middle tier on the Featured evals at the cadence M3 says we can afford. Exhaustive audits stay on request.

Done when: every eval in the registry has a findings page less than a month old.

### M6. QA and documentation (December, continuous from M2)

Recall check against the gold-standard manual audits named in the strategy doc: FORTRESS, BixBench, SciCode, extending the M2b measurement from deterministic defects to agentic ones. Fix what Auditor misses where the fix is deterministic. Add a human QA step before any finding changes the state of a contributor issue. Update Auditor docs and the Inspect Evals contributing docs to point contributors at the findings.

Done when: recall against the three gold audits is measured and written down, and CONTRIBUTING.md in Inspect Evals links to the hosted findings.

## Decisions still open

- Land `dev/integrated-audits` on `main`, or keep working from it. Needs a conversation with James. Everything above is built on the dev branch.
- Backlog shape: GitHub issues plus findings database, or GitHub Projects. See M4.
- Whether the Inspect Evals view files issues automatically or only drafts them. Start with drafts. Auto-closing contributor issues needs a confidence field on findings and a QA step; neither exists yet.
- Total cost of rollout. Nothing in the Q4 plan estimates it. Investigations ran at about ten dollars local budget plus Hawk jobs, and evals without logs need benchmark runs on top. M3 turns this into a number for Justin.
- Whether `gl-audit@2` is final. Laurence's doc is a proposal; confirm the seven dimensions and the five Implementation contributions before M2 renders against them.

## Dependencies on other people

- James: the SciCode investigation directory (`work/report/` registers and `published/`), and the branch decision.
- Laurence: confirmation of the taxonomy v2 dimensions and contributions.
- Justin and Tania: review of the M2 issues view. They are its users. Tania or Aleksey for the Inspect Evals side of the run-time pop-up and log links.
- Aleksey: the interface his maintenance agent expects from the findings and the +1 signal.
- Hawk operators: nothing beyond `hawk login`. The 3.5.0 CLI extra omits `aiofiles`; the `remote` extra here adds it and an upstream issue should be raised.

## Adjacent work not in this plan

Matt's other Q4 items sit in Inspect Evals, not here: patching security problems and merging the HLE updates to main. The template adoption PR #5 fixes the red CI on this repo and lands independently of PR #4.
