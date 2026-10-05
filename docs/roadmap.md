# Inspect Auditor roadmap, Q4 2026

Owner: Matt Fisher. Last revised 2026-10-05, re-cutting milestones 3 to 5 around the v1 architecture; 2026-10-01 added the rule-mining track from the PR triages; 2026-09-29 the [prototype review](findings-prototype-review.md) narrowed the first release. This is the working plan for Outcome 3 of the GL Q4 plan: moving Inspect Evals maintenance onto Inspect Auditor. Prose reports, scorecards and the exhaustive audits (Outcomes 1 and 2) stay with James and Laurence; this doc covers only where their outputs meet ours.

Revise this doc when a milestone lands or a decision below is made. Implementation detail belongs in the [design spec](superpowers/specs/2026-09-25-findings-prototype-design.md) and the [schema doc](finding-schema-envelope.md), not here.

## Goal

Every eval in Inspect Evals has a current, hosted set of findings that a maintainer can act on and a user can consult before trusting a number. Contributors are pointed at those findings instead of at the issue tracker. Findings come from deterministic producers everywhere and from bounded agentic investigation on the Featured evals, with exhaustive audits on request.

The first release is smaller than that: a short, reproducible list of maintenance issues for a handful of pilot evals that maintainers actually use. Everything else expands from it once its usefulness and cost are measured.

## Principles

- Auditor uses inspect-evals-lint and inspect-dataset as producers. The codebases are not merged.
- Findings are records in one envelope schema with the producer's own output kept verbatim. Assessments and coverage are separate records, not findings.
- A fingerprint identifies an observation. An issue gets its own durable id when a person accepts it, and observations link to it. Review decisions (suppressions, issue links) live in files beside the runs, never inside them.
- Run files are immutable. A sweep appends runs and moves a per-eval `current.json`; every view renders from that manifest.
- Unsupported inputs, failed producers and unassessed checks are shown beside findings. A missing finding can mean a defect class we do not cover, and a page must say so.
- The taxonomy is versioned data, not code. Old reports stay valid under `gl-audit@1`; the pilot renders original identifiers and does not wait on `gl-audit@2` being confirmed.
- Grades are written by people and are never computed. Nothing changes the state of a contributor issue without a human looking.
- Inspect Auditor is an installable Inspect package whose behaviour lives in skills. It has no GUI. The hosted site is a separate static thing that reads the findings.
- One owner for the finding schema: Matt. Report formats read from it.
- Rollout is tiered by cost. SciCode-depth audits do not scale to 130 evals and do not need to.
- Audit outputs and agent working notes are private until published. Nothing under `agent_artefacts/` or `artefacts/` enters this repo.

## Where we are

Done on `findings-prototype` (PR #4 against `dev/integrated-audits`), rebased onto the dev tip on 2026-09-29:

- Envelope schema, fingerprinting, immutable run files with `current.json`, parquet carrying every envelope field.
- Adapters for inspect-evals-lint, inspect-dataset and `.eval` log headers. Header findings carry the log's own revision; the run records the checkout it compared against.
- Versioned taxonomies `gl-audit@1` and draft `gl-audit@2` with a mapping.
- Hawk access: `hawk:` log sources, `--hawk-task`, `hawk-sets`, `hawk-pull` over `scripts/hawk-artefacts.yaml`. The chess investigation bundle is on disk.
- An acceptance sweep over six evals. Its lesson: without input selection, three of five header checks are dominated by mock and variant runs, and five of six dataset scans skipped.

Known and worth a person's time now: strong_reject records 313 samples where the eval declares 324.

## Milestones

Five. Milestones 3 to 5 were re-cut on 2026-10-05 around [v1-architecture.md](v1-architecture.md); the measurement in milestone 2 runs beside them rather than ahead. Dates follow once the pilot evals and acceptance criteria are agreed with Tania.

### 1. A usable local pilot

Make the deterministic pass trustworthy on a few pilot evals and import the first investigation.

- Input selection (done 2026-09-30, PR pending). Dataset scans take path, config, split, revision and field mapping from a declared per-eval configuration where inference is unreliable, and record what they examined. Header checks partition logs by task and task arguments, distinguish benchmark attempts from mock runs and sample-audit runs, and name the comparison revision. Skips and unsupported inputs render beside findings.
- Import the chess bundle: `findings.json` into the envelope, `assessments.json` and `coverage.json` kept as their own records and rendered together. Add the reconciled SciCode registers as a second fixture when James hands them over; raw worker labels stay distinguishable from reconciled results.
- Grouping and review files (done 2026-09-30, PR pending). Group observations by producer and rule with a count and an example. Apply reviewed suppressions from `suppressions.yaml`. Accepted candidates get an issue id in `issues.yaml`.
- Producer output as leads for the agents. A `leads` module renders one eval's current, suppressed, grouped findings as a short `LEADS.md` with record ids and locations, plus a `leads` subcommand so a person can see what an agent would be given. The investigator's `prepare_workspace` stages it under `/inputs/findings/` and names it in `seed.json`; the sample auditor stages the findings for its one sample as `/audit/leads.md` beside `discrepancies.md`. Both skills say the same thing they already say about worker verdicts: a lead is a hypothesis with a location attached, to confirm or retire, citing its record id in the register. The chess import then reads those citations back into status history, so a lint observation and the investigator's verdict on it become one record's story. The module and subcommand land in `findings`; the two staging hooks and the skill text go as one short PR to James after `core/changes` settles. Order: after grouping, because ungrouped leads would flood the agent's context.

Done when maintainers can pick actionable candidates from the pilot output and see the inputs, revision, checks, skips and unassessed areas behind them, re-rendering preserves the selected runs and every review decision, and an investigation started with `LEADS.md` cites at least one lead in its register.

### 2. Measure usefulness and cost

- Historical defects, before and after. The labelled set already exists: the 2026-09-09 scoring batch, the 2026-09-15 and 2026-10-01 PR triages and their rule-check verdicts, about sixty scoring cases. Write them as `cases.yaml` entries (eval, pre-fix revision, post-fix revision, class, expected finding, verdict), run the producers at both revisions, and report recall at pre, false positives at post, and precision on main, per rule and per class. Hold some cases out from rule development. Separate what deterministic producers catch from what needs an auditor item. See [maintenance-loop.md](maintenance-loop.md).
- Bounded investigations on two or three Featured evals: source and logs, no commissioned experiments. Record cost, wall time and findings beyond the deterministic pass. Cost any benchmark runs needed to supply logs separately. Run the auditor items the triage marked "not lintable" (`answer-format`, `gold-answer`, `failure-attribution`) on the evals where those classes recur, and measure them against the same cases.
- For the maintainer pilot record accepted issues, review time, recall, false positives and cost per accepted new issue.

Done when those numbers exist against criteria agreed with maintainers beforehand, and the measurement can be regenerated by one command.

### 3. Stand up the store, the review API and the scheduled run

Per [v1-architecture.md](v1-architecture.md).

- A `Store` API with four operations (suppress, accept, link, set status) that resolves what a reviewer points at into fingerprints, validates, writes `suppressions.yaml` and `issues.yaml`, and commits with the reviewer as author. A `review` CLI on it, and a promote action that files the GitHub issue and records the link in one step. Nobody edits the review files by hand.
- A findings MCP server on the same API: read tools `leads`, `finding`, `search`, `issues`, `inputs`; write tools `set_status`, `accept`, `suppress` with the agent's run id as provenance. `leads` runs the producers lazily when the store has no current view for the eval at the requested revision. Local stdio mode first; the Hawk egress and token questions go to James with the attach PR.
- A private repository, `Generality-Labs/inspect-evals-findings`, holding `runs/`, `current.json`, `suppressions.yaml`, `issues.yaml`, `cases.yaml` and an `export/` directory.
- The current view becomes derived (newest run per eval and producer), with `current.json` kept only for explicit pins, so concurrent writers never conflict on a shared file. Producer runs commit directly; review decisions take the review path.
- A scheduled Action that checks out Inspect Evals main, runs the deterministic producers over every eval (lint, dataset scans through each task, header checks where logs are on Hawk), commits the runs and moves `current.json`.
- An export step on merge: `findings.parquet`, `runs.parquet`, one JSON per eval, an index JSON. Publication selects what leaves the repo; native producer records stay private until the standard says otherwise.

Done when the Action has run unattended for a week, every eval in the registry has a current view, a `review accept` from the CLI changes the export without anyone opening a YAML file, and an agent can pull leads for an eval over MCP.

### 4. The table site

- A static site at `audits.generality.org` in its own repository, deployed with Cloudflare Pages from the worker template, separate from the Quarto blog.
- An index of findings across every eval, filterable and sortable on eval, producer, dimension, check, severity, status and reviewed; a second index of evals with counts, accepted issues and last successful run.
- A page per eval: Inputs, grouped findings, Suppressed, Issues, Assessment and Coverage where an investigation exists, links to bundles and GitHub issues. No grade.
- Review actions on the eval page (suppress, accept, promote) through a Worker with GitHub auth that calls the Store API and commits as a bot, opening PRs at first.
- Voting through reactions on the promoted GitHub issues. No custom service.
- Before it is public, re-read the security review that removed the frontend from the dev branch, and review the publication path.

Done when a maintainer can find an eval, see what was checked and when, pick an accepted issue and land on its GitHub page, and a reader cannot mistake "nothing found" for "nothing checked".

### 5. Import the existing investigations and expand

- The register adapter imports the chess bundle and the SciCode registers into the findings repo as PRs; Assessment and Coverage render on those evals' pages; cited leads become status history.
- The MCP attach step, the staged-file fallback and the skill text go to James as one PR, with the egress allowlist and token scope questions.
- Deterministic checks across the registry are already scheduled by milestone 3; bounded investigations run at the cadence milestone 2 says we can afford. Each eval page shows which checks apply and which have run.

QA runs through every milestone. Recall against the gold-standard manual audits (FORTRESS, BixBench, SciCode) extends the milestone 2 test set as the matching investigation capability lands.

## Rule mining: the fortnightly track

The 2026-10-01 PR triage and its rule-check pass are the process this track repeats every fortnight: crawl and cluster the open PRs, mine a candidate check per cluster, sort it into lint rule, shared helper, test convention, review checklist or auditor item, raise the lint issue and the "agree before lint encodes it" policy issue, check the draft rule against the PRs as test cases, and hand maintainers the action list. [maintenance-loop.md](maintenance-loop.md) records the loop, the `cases.yaml` format, the metric, the cluster-to-taxonomy mapping and the four skills to distil: `pr-triage`, `rule-mining`, `rule-check`, `policy-question`. Its outputs are issues, lint rules, helper proposals, policy questions and docs, which is the forward-deployed-engineer role the Q4 plan assigns: no code is committed to Inspect Evals.

Two rules of the track. A rule whose policy question is open does not ship; the 1 October rule-check found four RULE-WRONG verdicts against draft rules that read as solid, so the policy step is where the loop earns its keep. And precision on main is not recall: hit counts say what a rule flags today, only the pre-fix revisions say what it would have caught.

Sequence: first run of the loop against the existing corpora inside milestone 2; the four skills written as the measurement is built, since the rule-check brief already exists in prose; the `pr-triage` crawl becomes a producer when the second run shows it is stable.

## Report from the store: a separate track

Rendering the GL LaTeX report from the findings store, with lint and dataset findings alongside the investigator's, is proposed to James and Laurence separately. It needs the Assessment and Coverage records above, and a taxonomy data file the `.sty` is generated from. It does not block the pilot and does not start until the pilot has consumed real records. The investigator keeps its own authoring format throughout; conversion happens at publish.

## Decisions still open

- Pilot evals and acceptance criteria, agreed with Tania. First.
- Land `dev/integrated-audits` on `main`, or keep working from it. James's `core/changes` is heading into dev; both our PRs target dev.
- Backlog shape (GitHub issues plus findings database, or GitHub Projects) and any custom voting. v1 uses `issues.yaml` plus GitHub issues with reactions; revisit after the site has run.
- Findings repo name and write access; nightly or weekly schedule; site domain; whether the export itself is public. Proposals in v1-architecture.md.
- Whether the view files issues automatically or only drafts them. Drafts. Auto-closing needs a confidence field and a QA step; neither exists.
- Total cost of rollout. Milestone 2 turns it into a number for Justin.
- Whether `gl-audit@2` is final. Confirm with Laurence separately; the pilot does not depend on it.
- Where `cases.yaml` lives (findings output beside the review files, or Inspect Evals `tests/cases/`), and how a merged PR gets its defect class without asking the contributor. See maintenance-loop.md.

## Dependencies on other people

- James: the SciCode investigation directory, the branch decision, a view on rendering the report from the store, and for the MCP server: Hawk egress to its host and a scoped write token in the runner.
- Laurence: the v2 dimensions and contributions, when convenient.
- Tania and Justin: the policy questions the rule-check raises (inspect_evals #2599 to #2602 today); a lint rule waits on its question. Tania: pilot evals, acceptance criteria, review of the pilot output. Tania or Aleksey for the Inspect Evals side of run-time links to findings, once a URL exists.
- Aleksey: what his maintenance agent expects from findings and issues.
- Hawk: nothing beyond `hawk login`. The server is on 3.6.0; the 3.5.0 CLI extra omits `aiofiles`, which the `remote` extra here adds.

## Adjacent work not in this plan

Matt's other Q4 items sit in Inspect Evals, not here: patching security problems and merging the HLE updates to main. Contributor-policy changes in the Q4 plan are Justin's decision and should follow measured coverage and maintainer workload, not precede them.
