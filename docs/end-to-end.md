# From a sweep to the site: an end-to-end walk-through

This follows one eval, BFCL, through every step: from the deterministic checks to the table site. Every command below was run for real on 2026-10-15 into a local scratch store, and the JSON excerpts are that run's output, trimmed. The investigation in step 5 is a real one, run on 2026-10-12.

inspect_audit has two parts, and this walk-through uses both. Steps 1 to 4, 6 and 7 are the **findings store**: the `inspect-audit-findings` CLI and the store it writes. Step 5 is the **investigator agent**, an Inspect task that is separate software: it is run with `inspect eval`, costs model calls, and writes its own folder outside the store. The two meet at step 4 (leads, from the store to the investigator) and step 6 (import, from the investigator to the store).

| Step            | Part                                              | Command                                        | Writes to the store?                   |
| --------------- | ------------------------------------------------- | ---------------------------------------------- | -------------------------------------- |
| 1 · Sweep       | findings store                                    | `inspect-audit-findings run`                   | yes: run records                       |
| 2 · Render      | findings store                                    | automatic, or `inspect-audit-findings summary` | yes: derived files only                |
| 3 · Review      | findings store                                    | `inspect-audit-findings review …`              | yes: review decisions                  |
| 4 · Leads       | findings store, for the investigator              | `inspect-audit-findings leads --bundle`        | no: writes a folder for the agent      |
| 5 · Investigate | **investigator agent**                            | `inspect eval inspect_audit/investigate`       | no: writes an investigation folder     |
| 6 · Import      | findings store, reading the investigator's output | `inspect-audit-findings import-investigation`  | yes: a run record and review decisions |
| 7 · Site        | the table site                                    | nothing                                        | no: reads the export                   |

The store in this walk-through is a local folder. A deployment's store is usually a bucket: every command takes `--store s3://<bucket>` instead and writes the same files there. Generality's deployment is described in [deployments/generality.md](deployments/generality.md).

## The words, first

The word "finding" is used for four different things in this system. This guide never uses it bare.

| Term in this guide    | What it is                                                                                                                                                                                                                  | Who makes it                                             | Where it lives                                                  |
| --------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------- | --------------------------------------------------------------- |
| **Raw result**        | One item of a producer's own output, in that producer's own format. inspect-dataset calls its items "Finding" too; the investigator calls its rows "findings".                                                              | The producer tool                                        | The producer's output files (step 1 scan folder, step 5 bundle) |
| **Producer**          | A tool that examines an eval: `inspect_evals_lint`, `inspect_dataset`, the log-header checks, and now `inspect_audit_investigate`                                                                                           |                                                          |                                                                 |
| **Run record**        | One producer's pass over one eval. Holds outcomes, Finding records and Assessment records. Never edited after it is written.                                                                                                | The findings CLI, converting the producer's raw results  | `<eval slug>/runs/<run id>.run.json` in the store               |
| **Outcome**           | One rule or check of a producer, with `pass`, `fail` or `skip`. Says whether a check ran and what it saw, not where.                                                                                                        | Converted from the producer's output                     | Inside a run record                                             |
| **Finding record**    | One occurrence of one problem at one place. The same shape whatever the producer; the producer's raw result is kept inside it as `source.record`.                                                                           | The findings CLI                                         | Inside a run record (`findings[ ]`)                             |
| **Assessment record** | One check's verdict under a named methodology, for example "check C.1 of inspect-audit@1 is Minor". A judgement, not an occurrence. Clean and not-assessed checks are assessments too. Added with the investigation import. | Only the investigation import, so far                    | Inside a run record (`assessments[ ]`)                          |
| **Record id**         | The id of a Finding record: `<run id>/<n>`. Names this occurrence in this run.                                                                                                                                              | Assigned in order when the run record is built           | `findings[ ].id`                                                |
| **Fingerprint**       | A hash of producer, rule, eval and primary location. Names the same problem across runs.                                                                                                                                    | Computed once, when the Finding record is written        | `findings[ ].fingerprint`                                       |
| **Review decision**   | A person's ruling: suppress, accept as an issue, link, set status, retract. Append-only.                                                                                                                                    | `review …` commands, and the import on a person's behalf | `review/<stamp>-<decision id>.json`                             |
| **Issue**             | A problem someone has accepted, grouping Finding records by fingerprint, for example `ISS-0001`                                                                                                                             | `review accept`                                          | Derived from decisions; shown in the export                     |
| **Lead**              | A Finding record as shown to the investigator, in `LEADS.md`                                                                                                                                                                | `leads` command                                          | `LEADS.md`                                                      |
| **Bundle**            | Everything an investigation publishes: its report, its raw results (`findings.json`, `assessments.json`, `coverage.json`) and its evidence. Not in the store; the import reads it.                                          | The investigator                                         | `<investigation dir>/published/<bundle id>/`                    |
| **Current view**      | For each eval, the newest run record of each producer. Older run records stay as history.                                                                                                                                   | Computed at render                                       | Not a file                                                      |
| **Export**            | JSON the site reads, rebuilt from the current view and the review decisions                                                                                                                                                 | Render                                                   | `export/index.json`, `export/evals/<slug>.json`                 |

The short version: producers write **raw results** in their own formats. The findings CLI turns each producer pass into one **run record** holding **outcomes**, **Finding records** and, for investigations, **Assessment records**. People add **review decisions**. Everything else is derived.

## What the store looks like at the end

```
store/
  inspect-evals-bfcl/                 one folder per eval (the eval slug)
    runs/                             run records: written once, never edited
      inspect_dataset-20261008T050851Z-inspect-evals-bfcl.run.json      earlier sweep (history)
      inspect_evals_lint-20261008T050859Z-inspect-evals-bfcl.run.json   earlier sweep (history)
      inspect_dataset-20261015T033725Z-inspect-evals-bfcl.run.json      step 1 (current)
      inspect_evals_lint-20261015T033751Z-inspect-evals-bfcl.run.json   step 1 (current)
      inspect_audit_investigate-20261012T082031Z-inspect-evals-bfcl.run.json   step 6 (current)
    scans/<run id>/                   inspect-dataset's raw output (step 1)
    SUMMARY.md                        derived
  review/                             review decisions: append-only
    20261015T033857Z-dec-…97852934a.json    step 3 (accept)
    20261015T033900Z-dec-…17823652c.json    step 6 (status, from the import)
  export/index.json                   derived: what the site reads
  export/evals/inspect-evals-bfcl.json      derived: what the site reads
  SUMMARY.md, findings.parquet, runs.parquet, suppressions.yaml, issues.yaml   derived
```

Two kinds of thing are written by commands: run records and review decisions. Everything marked derived is rebuilt from those two on every render, and can be deleted and regenerated.

______________________________________________________________________

## Step 1 · Sweep: run the deterministic producers (findings store)

```bash
uv run inspect-audit-findings run --root ../inspect_evals --store <store> inspect_evals/bfcl
```

`--root` is an Inspect Evals checkout. It took 27 seconds and printed nothing.

What happens inside, per producer:

1. The CLI runs the producer tool as a subprocess. The exact command is recorded in the run record's `inputs.argv`:
   - lint: `uvx --from inspect-evals-lint==0.10.0 inspect-evals-lint --root …/inspect_evals bfcl --output-format json`
   - dataset: `uvx --from inspect-dataset[inspect]==0.6.0 … inspect-dataset scan …/_replay_samples.py@replay_samples -o <scan folder>`, which loads BFCL through its own task loader and scans every sample.
2. The tool writes its **raw results**. Lint prints JSON to stdout; the CLI parses it and keeps no separate file. inspect-dataset writes a folder.
3. The CLI's adapter for that producer converts the raw results into one **run record**: an **outcome** per rule, and a **Finding record** per raw result.
4. The run record is written to `<slug>/runs/<run id>.run.json`, then everything is rendered (step 2).

Files produced:

```
inspect-evals-bfcl/runs/inspect_dataset-20261015T033725Z-inspect-evals-bfcl.run.json     200 Finding records, 13 outcomes
inspect-evals-bfcl/runs/inspect_evals_lint-20261015T033751Z-inspect-evals-bfcl.run.json  0 Finding records, 31 outcomes
inspect-evals-bfcl/scans/inspect_dataset-20261015T033725Z-inspect-evals-bfcl/
  scan_summary.json  duplicate_questions.json  encoding_issues.json  samples.json  REPORT.md
```

The run id is `<producer>-<UTC timestamp>-<eval slug>`. The scan folder is the dataset producer's raw output. In a local store all of it is kept; `samples.json` holds every sample's text (2 MB for BFCL). In the bucket only `scan_summary.json` is uploaded, because some datasets are gated and their text must not leave the sweeping machine.

**A raw result** (inspect-dataset's own format, from `scans/…/duplicate_questions.json`):

```json
{
  "scanner": "duplicate_questions",
  "severity": "high",
  "explanation": "3 samples share the question, with the same answer '' (at indices [240, 1298, 1305]). This is likely a duplicated sample.",
  "sample_index": 240,
  "sample_id": "irrelevance_0",
  "metadata": {
    "question": "calculate the area of a triangle given the base is 10 meters and height is 5 meters.",
    "duplicate_ids": ["irrelevance_0", "live_irrelevance_835-326-0", "live_irrelevance_841-331-0"]
  }
}
```

**The Finding record made from it** (in the dataset run record, trimmed):

```json
{
  "id": "inspect_dataset-20261015T033725Z-inspect-evals-bfcl/1",
  "fingerprint": "sha256:ddaae195…",
  "producer": "inspect_dataset",
  "rule": "duplicate_questions",
  "dimension": "dataset",
  "severity": "major",
  "status": "supported",
  "summary": "3 samples share the question, with the same answer '' (at indices [240, 1298, 1305]). This is likely a duplicated sample.",
  "locations": [{"kind": "sample", "role": "primary", "dataset": "inspect_evals/bfcl", "sample_id": "irrelevance_0"}],
  "source": {
    "format": "inspect_dataset.Finding@0.6.0",
    "record": {"scanner": "duplicate_questions", "…": "the raw result, with the question cut to 32 characters: 'calculate the area of a triangle…'"}
  }
}
```

What changed between the two: the producer's `severity: high` became the store's `severity: major`; the sample became a `sample` location; the producer and rule are named; the record got an id and a fingerprint; and the raw result was kept, with sample text cut to 32 characters.

**Outcomes** (in the same run record) say which checks ran:

```json
[
  {"rule": "answer_distribution", "status": "pass", "message": "0 finding(s)"},
  {"rule": "answer_length", "status": "skip", "message": "answer_length assumes a scorer that compares answer text verbatim; this task scores with inspect_evals/bfcl_scorer"},
  {"rule": "duplicate_questions", "status": "fail", "message": "197 finding(s)"}
]
```

A third producer, the log-header checks, runs only when `--logs` names Inspect logs. It was not run here.

## Step 2 · Render (findings store, automatic)

Every command that writes something ends by rendering. To render by hand:

```bash
uv run inspect-audit-findings summary --store <store>
```

Render reads every run record and every review decision, then:

1. picks the **current view**: for each eval, the newest run record per producer. Here that is the 15 October lint and dataset runs; the 8 October runs (copied from the bucket, to mirror real history) are kept but not current;
2. folds the review decisions into a review: suppressions, issues, statuses;
3. writes the derived files.

| Derived file                       | What it holds                                                                                                    | Read by                   |
| ---------------------------------- | ---------------------------------------------------------------------------------------------------------------- | ------------------------- |
| `export/index.json`                | every eval's run summary, and every active Finding record as a table row                                         | the site's findings table |
| `export/evals/<slug>.json`         | one eval: inputs, current runs with outcomes and assessments, grouped Finding records, suppressed groups, issues | the site's eval page      |
| `<slug>/SUMMARY.md`, `SUMMARY.md`  | the same, as Markdown                                                                                            | people                    |
| `findings.parquet`, `runs.parquet` | flat tables                                                                                                      | analysis                  |
| `suppressions.yaml`, `issues.yaml` | the folded review, readable                                                                                      | people                    |

Run records are never changed by render. A Finding record's status, suppressions and issue on the page come from applying review decisions to a copy at render time.

## Step 3 · Review (findings store; optional, a person)

A person looks at the page and rules. Here, accepting the three control-character Finding records as one issue:

```bash
uv run inspect-audit-findings review accept --store <store> --author "Ada Lovelace <ada@example.com>" \
  --eval inspect_evals/bfcl --rule encoding_issues --title "BFCL questions contain control characters"
# accepted ISS-0001: BFCL questions contain control characters (3 observation(s), dec-20261015T033857Z-97852934a)
```

File produced, `review/20261015T033857Z-dec-20261015T033857Z-97852934a.json`:

```json
{
  "id": "dec-20261015T033857Z-97852934a",
  "at": "2026-10-15T03:38:57.978529Z",
  "author": "Ada Lovelace <ada@example.com>",
  "kind": "accept",
  "accept": {
    "issue": "ISS-0001",
    "title": "BFCL questions contain control characters",
    "subject": "inspect_evals/bfcl",
    "fingerprints": ["sha256:5787df19…", "sha256:74ad94c8…", "sha256:854b38c7…"]
  }
}
```

The decision names Finding records by **fingerprint**, not record id, so it keeps applying when the next sweep writes new run records with new record ids. The command re-renders: the issue appears in the export's `issues` and in `LEADS.md`.

The other review commands, one line each: `review suppress` hides a rule's Finding records as noise; `review status` sets a Finding record's status; `review link` attaches a GitHub issue URL; `review retract` withdraws an earlier decision.

## Step 4 · Leads: what the investigator is given (findings store → agent)

```bash
uv run inspect-audit-findings leads --store <store> --bundle leads/bfcl inspect_evals/bfcl
```

Files produced:

```
leads/bfcl/LEADS.md                                                     the leads, as Markdown
leads/bfcl/runs/inspect_dataset-20261015T033725Z-inspect-evals-bfcl.run.json   copies of the current run records,
leads/bfcl/runs/inspect_evals_lint-20261015T033751Z-inspect-evals-bfcl.run.json  with the review applied
```

`LEADS.md` lists the current Finding records of the eval, grouped by producer, rule and severity, three per group, each by **record id**:

```
## Already accepted as issues
- ISS-0001 · BFCL questions contain control characters · 3 current observations

### inspect_dataset · duplicate_questions · dataset · major · 119 observations
- `inspect_dataset-20261015T033725Z-inspect-evals-bfcl/1` · `sample:inspect_evals/bfcl:irrelevance_0` · 3 samples share the question …
- and 116 more in `runs/inspect_dataset-20261015T033725Z-inspect-evals-bfcl.run.json`
```

Suppressed Finding records are left out. Investigation run records are left out, so one investigation never feeds the next. The `runs/` copies are there so the "and 116 more" pointers resolve inside the investigator's container.

## Step 5 · Investigate (the investigator agent; the real run of 2026-10-12)

```bash
uv run inspect eval inspect_audit/investigate \
  -T repo=../inspect_evals \
  -T target_task=inspect_evals/bfcl \
  -T 'paths=["README.md","pyproject.toml","src/inspect_evals/__init__.py","src/inspect_evals/bfcl","src/inspect_evals/constants.py","src/inspect_evals/metadata.py"]' \
  -T leads=<leads dir> -T execution=local -T budget_usd=10 \
  -T output_dir=<investigation dir>/output \
  --model openrouter/openai/gpt-6-sol --log-dir <investigation dir>/logs
```

This step is the investigator agent, not the findings store: none of the store's code runs here, and the agent reads only the leads folder from step 4. `-T paths=…` works around the path bug described under known gaps. That run used leads rendered from the bucket's 8 October sweep, so the record ids it cites are `inspect_dataset-20261008T050851Z-…`, not the 15 October ones above.

Files produced, under the investigation folder:

```
logs/2026-10-12T08-08-18-00-00_investigate_….eval      the Inspect log of the agent's run
output/<investigation id>/
  inputs/                     what the agent was given, mounted read-only at /inputs
    seed.json                 repo, revision, paths, task, budget, and "leads": "/inputs/findings/LEADS.md"
    source/                   committed snapshot of the paths above
    findings/LEADS.md, findings/runs/   the leads bundle from step 4
  work/                       the agent's workspace: journal.md, downloaded data, the report as drafted
  published/<bundle id>/      THE BUNDLE: what the import reads
    findings.json             the investigator's raw results: one row per problem it reports
    assessments.json          the investigator's raw results: one row per framework check
    coverage.json             how many dataset items it labelled (here: none of 4981)
    metadata.tex              includes \commitref{dbd3dd25}, the revision audited
    report.pdf, report.tex, Findings.tex, assessments.tex   the human-readable report
    evidence/                 scripts and outputs the agent made (probes, profiles)
    framework/                the GL report framework, including checks.json (A, A.1, …)
    _inputs/                  copies of the inputs it cited (source files, LEADS.md)
```

The bundle id here is `a5198d4b85aa45748841c5cea06ca318`. Nothing in this step touches the store.

**A raw result: one row of `findings.json`** (trimmed):

```json
{
  "id": "contradictory-relevance",
  "section": "dataset",
  "claim": "Three identical question-and-tool configurations appear both as irrelevant (no call rewarded) and as relevant (specific tool call rewarded).",
  "status": "supported",
  "origin": "source",
  "severity": "Minor",
  "evidence": [
    {"path": "evidence/pinned_exemplars.json", "location": "three relevance pairs …"},
    {"path": "evidence/pairs_probe.txt", "location": "three paired cases"},
    {"path": "_inputs/source/src/inspect_evals/bfcl/score/scorer.py", "location": "relevance_match, irrelevance_match and ast_match"},
    {"path": "_inputs/findings/LEADS.md", "location": "inspect_dataset-20261008T050851Z-inspect-evals-bfcl/3"}
  ]
}
```

The `id` is a name the agent chose. The last evidence entry is a **lead citation**: its path is `LEADS.md` and its location is the record id of the Finding record it rests on. That is the only place the investigator refers to anything in the store.

**A raw result: one row of `assessments.json`**:

```json
{
  "id": "C.1",
  "assessment": "Minor",
  "result": "Same question plus tools receives incompatible target actions in three pairs; both labels cannot be correct.",
  "evidence": ["evidence/pairs_probe.txt", "/inputs/source/src/inspect_evals/bfcl/score/scorer.py"]
}
```

`C.1` is a check of the GL report framework: dimension C (dataset), contribution 1 (in `framework/checks.json`). Those ids are the same as the `inspect-audit@1` taxonomy's. This bundle has 56 such rows: 8 Minor or Major, the rest Not assessed.

## Step 6 · Import the bundle into the store (agent → findings store)

```bash
uv run inspect-audit-findings import-investigation --store <store> --eval inspect_evals/bfcl \
  --timestamp 2026-10-12T08:20:31Z --author "Ada Lovelace <ada@example.com>" \
  <investigation dir>/output/<investigation id>/published/a5198d4b85aa45748841c5cea06ca318
# imported a5198d4b85aa45748841c5cea06ca318 as inspect_audit_investigate-20261012T082031Z-inspect-evals-bfcl:
#   2 finding(s), 56 assessment(s), 1 lead(s) marked
```

What happens inside:

1. **Read the bundle.** Each `findings.json` row becomes a Finding record; each `assessments.json` row becomes an Assessment record; both go into one new run record of the producer `inspect_audit_investigate`. Coverage counts and the bundle's id, path and a hash of `findings.json` go in the run record's `inputs`. Rows with `origin: audit_limitation` are not benchmark problems, so they are listed in `inputs.limitations` instead of becoming Finding records.
2. **Check it is new.** If a run record already carries this bundle id, nothing new is written (a re-import only finishes marking leads).
3. **Resolve lead citations.** For each supported or qualified row that cites a lead, look up the cited record id (`…20261008…/3`) in all run records, history included, and take its fingerprint (`sha256:651c955d…`). That fingerprint is still in the current view, now as record `…20261015…/3`, so the lead can be marked.
4. **Write.** The run record goes to `inspect-evals-bfcl/runs/`; one status decision per marked lead goes to `review/`; then render.

**The Finding record made from the `findings.json` row** (in the new run record, trimmed):

```json
{
  "id": "inspect_audit_investigate-20261012T082031Z-inspect-evals-bfcl/1",
  "fingerprint": "sha256:7c25d07c…",
  "producer": "inspect_audit_investigate",
  "rule": "contradictory-relevance",
  "taxonomy": "inspect-audit@1",
  "dimension": "dataset",
  "severity": "minor",
  "status": "supported",
  "summary": "Three identical question-and-tool configurations appear both as irrelevant …",
  "locations": [
    {"kind": "artifact", "role": "primary", "path": "evidence/pinned_exemplars.json", "location": "three relevance pairs …"},
    {"kind": "artifact", "role": "related", "path": "evidence/pairs_probe.txt", "location": "three paired cases"}
  ],
  "source": {"format": "inspect_audit.investigation.finding", "record": {"…": "the findings.json row"}}
}
```

The agent's `id` became the `rule`; `section` became `dimension`; `claim` became `summary`; the evidence became locations, minus the lead citation, which became a status decision instead.

**The Assessment record made from the `assessments.json` row** (the newest record type):

```json
{
  "methodology": "inspect-audit@1",
  "check": "C.1",
  "level": "Minor",
  "notes": "Same question plus tools receives incompatible target actions in three pairs; both labels cannot be correct.",
  "evidence": [
    {"kind": "artifact", "role": "related", "path": "evidence/pairs_probe.txt"},
    {"kind": "artifact", "role": "related", "path": "/inputs/source/src/inspect_evals/bfcl/score/scorer.py"}
  ],
  "findings": [],
  "measure": null,
  "impact": null,
  "fields": {}
}
```

`findings` is empty because this row's evidence names files, not the bundle's own finding ids. When a row does name one (the chess bundle does), `findings` holds that Finding record's **fingerprint**. When a row lists the items it checked and the ones affected, `measure` holds the counts.

**The status decision for the lead**, `review/20261015T033900Z-dec-20261015T033900Z-17823652c.json`:

```json
{
  "id": "dec-20261015T033900Z-17823652c",
  "author": "Ada Lovelace <ada@example.com>",
  "kind": "status",
  "reason": "cited as evidence by inspect_audit_investigate-20261012T082031Z-inspect-evals-bfcl/1",
  "status": {"fingerprints": ["sha256:651c955d…"], "status": "supported"}
}
```

It has the same shape as a decision from `review status`, written on the importing person's behalf. The reason names the investigator's Finding record by record id, so a reader can follow it back.

## Step 7 · What the site reads (table site)

When the store is a bucket the site can read, a render is all it takes to update the page: the site reads `export/` directly (Generality's site uses an R2 binding). This walk-through used a local folder, so nothing here reached the site.

After step 6, BFCL's `export/evals/inspect-evals-bfcl.json` has three current runs: the investigator's, and the 15 October dataset and lint runs. The investigator's run entry carries its 56 assessments, with cited findings translated from fingerprints to record ids:

```json
{
  "run_id": "inspect_audit_investigate-20261012T082031Z-inspect-evals-bfcl",
  "producer": "inspect_audit_investigate",
  "assessments": [
    {"methodology": "inspect-audit@1", "check": "C.1", "level": "Minor", "notes": "Same question plus tools …", "measure": null, "findings": [], "evidence": ["…"]}
  ]
}
```

The investigator's two Finding records are rows in `groups`, next to the dataset producer's:

```
inspect_audit_investigate  agentic-containment      major    1
inspect_dataset            duplicate_questions      major  119
inspect_audit_investigate  contradictory-relevance  minor    1
inspect_dataset            duplicate_questions      none    78
inspect_dataset            encoding_issues          none     3   (ISS-0001)
```

`export/index.json` lists all 202 rows for the findings table.

## Known gaps this walk-through shows

- **The lead's confirmation is invisible.** The dataset producer already writes every Finding record as `supported`, so the investigator's `supported` changes nothing visible, and the export does not include status history. On the site, the lead looks exactly as it did before the import. Exporting history, or a "confirmed by" field, would fix this.
- **The site does not show assessments yet.** They are in the export; the eval page needs the audits-site change.
- **Assessment evidence uses container paths.** `/inputs/source/…` is a path inside the investigator's container, not a file in the bundle.
- **The investigator's path bug.** Without `-T paths=…`, it searched untracked folders in the checkout (`.claude/worktrees/…`), found a stray copy of BFCL there, and failed to archive it.
- **The checkout was behind.** The bucket's sweep saw BFCL task version 8-B; the local checkout the investigator audited was at 7-B.

## All the commands, in order

```bash
# 1. sweep (writes run records; renders)
uv run inspect-audit-findings run --root ../inspect_evals --store <store> inspect_evals/bfcl
# 2. render by hand (every other command already does this)
uv run inspect-audit-findings summary --store <store>
# 3. review (optional; writes a decision; renders)
uv run inspect-audit-findings review accept --store <store> --author "Name <email>" --eval inspect_evals/bfcl --rule encoding_issues --title "…"
# 4. leads for the investigator (writes a folder outside the store)
uv run inspect-audit-findings leads --store <store> --bundle leads/bfcl inspect_evals/bfcl
# 5. investigate (writes an investigation folder outside the store; costs money)
uv run inspect eval inspect_audit/investigate -T repo=../inspect_evals -T target_task=inspect_evals/bfcl -T leads=leads/bfcl -T execution=local --model openrouter/openai/gpt-6-sol
# 6. import the bundle (writes a run record and status decisions; renders)
uv run inspect-audit-findings import-investigation --store <store> --eval inspect_evals/bfcl --author "Name <email>" <investigation dir>/published/<bundle id>
# 7. nothing to run: when the store is the site's bucket, the site reads export/ directly
```
