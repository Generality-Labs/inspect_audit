# v1 architecture: one store, two products

Written 2026-10-05 after Matt and Justin agreed that a filter-and-sort table of findings across every eval is the key view. This records how v1 runs, where the findings store lives, how the table site and the blog relate to it, and the decisions still open. The [roadmap](roadmap.md) milestones 3 and 4 are cut around it.

## The shape

```mermaid
flowchart LR
    subgraph sources["Sources"]
        IE[(Inspect Evals main)]
        HAWK[(Hawk: logs, investigation bundles)]
    end
    subgraph store["Generality-Labs/inspect-evals-findings (private git)"]
        ACT[scheduled Action<br/>run producers over every eval]
        RUNS[runs/ + current.json<br/>immutable]
        REVIEW[suppressions.yaml<br/>issues.yaml<br/>cases.yaml]
        EXPORT[export step<br/>findings.parquet + per-eval JSON]
        ACT --> RUNS
        RUNS --> EXPORT
        REVIEW --> EXPORT
    end
    IE --> ACT
    HAWK -- hawk: addresses --> ACT
    HAWK -- registers, via adapter PR --> RUNS
    EXPORT --> SITE[audits.generality.org<br/>table site, Cloudflare Pages]
    EXPORT -- data/*.json --> BLOG[generality.org/blog<br/>human-authored Quarto posts]
    EXPORT --> LEADS[LEADS.md for the investigator<br/>and sample auditor]
    REVIEW -- accepted issues --> GH[Inspect Evals GitHub issues]
    SITE -.links.-> GH
```

Everything that finds problems writes to one store. Everything that shows them reads from it. The two public products are different renderings of the same records, not different data.

## The store

A private GitHub repository, `Generality-Labs/inspect-evals-findings`, holding:

- `runs/` and `current.json` per eval, written by the scheduled run and by investigation imports. Immutable; a sweep appends and moves the pointer. Hundreds of kilobytes per eval per run, which git handles for a long time.
- `suppressions.yaml` and `issues.yaml`, human-edited through pull requests, so every decision has an author, a reason and blame.
- `cases.yaml`, the labelled defects the measurement runs against.
- `export/`, rebuilt on every merge: `findings.parquet`, `runs.parquet`, one JSON per eval, and an index JSON. The export is what leaves the repo.

Why git and not R2 now: the review files want PRs; provenance is free; access control is the repo's; the data is small. R2 is where the export is published, and where the store moves only if runs grow past what git handles comfortably. Logs and evidence bundles stay on Hawk and are referenced by `hawk:` address with a checksum; the store never copies them.

## How it runs

1. **Scheduled run.** A GitHub Action in the findings repo, nightly, checks out Inspect Evals main, runs the deterministic producers over every eval (lint, dataset scans through each eval's task, header checks for the evals whose logs are on Hawk, resolved by `--hawk-task`), writes the runs and `current.json`, and commits. Exit code 1 (a producer skipped) is normal; the run records why.
2. **Review.** Matt or Tania edits `suppressions.yaml` and `issues.yaml` through PRs. Accepting a candidate assigns an issue id; filing it on Inspect Evals adds the GitHub URL. The export rebuilds on merge.
3. **Investigations.** Run on Hawk as now, publishing bundles to Hawk's S3. The register adapter imports `findings.json`, `assessments.json` and `coverage.json` into the findings repo as a PR, citing the bundle by address. Where an investigation cited leads by record id, the import writes status history on those observations.
4. **Leads.** The investigator and sample auditor stage `LEADS.md` from the latest export. The hooks are a separate PR to James.
5. **Export and deploy.** The export step runs on merge and publishes to the table site. Blog posts pull the JSON they need into their `data/` directories by hand, as the BixBench post already does with `audit_matrix.json`.
6. **Measurement.** The fortnightly rule-mining track runs `measure` against `cases.yaml` and records the table in the repo. It is not on the site.

## The table site

A small static site at `audits.generality.org`, in its own repository, deployed with Cloudflare Pages from the worker template. Separate from `Generality-Labs.github.io`, whose Quarto pipeline is for essays.

- **Index.** One row per finding across every eval, filterable and sortable on eval, producer, dimension, check, severity, status, reviewed (issue id or none), first seen, last seen. Default sort: accepted issues first, then raw observations grouped by rule with counts. A second index of evals: counts by severity, accepted issues, last successful run, which producers ran.
- **Eval page.** The Inputs section (what was scanned, which logs, what skipped), the grouped findings, the Suppressed and Issues sections, the Assessment table and Coverage where an investigation exists, links to the investigation bundle and to GitHub issues. No grade.
- **Honesty columns.** Every row shows who found it and whether a person has looked. Every eval page shows the date of the last successful run and the checks that did not run. A page with no findings must be distinguishable from a page nobody checked.
- **Voting.** Reactions on the promoted GitHub issues for now. No custom service in v1.
- **Data path.** Pre-rendered JSON per eval plus the index JSON, read by a React table. DuckDB-WASM over the parquet is an option if filtering across the whole corpus in the browser becomes slow; start without it.

## The blog

Unchanged as a product. Posts are human-authored Quarto documents with an argument, framing and a grade written by a person, which is what the strategy doc requires. They import exports from the store into `data/`: per-question verdicts (the audit matrix), assessment tables, example lists. The schema holds one sentence per finding and the producer's own text; it does not hold document-level prose, because a generated post is a future renderer over records plus a human overview, not a schema field.

## What goes in the table

Everything active, with provenance as columns, nothing gated behind the investigator. The deterministic producers are the only thing that covers 130 evals this quarter; routing their output through the investigator would leave the site mostly empty. The table is honest through its columns: a lint observation reads as a lint observation, an accepted issue reads as accepted, an investigator-confirmed lead carries its status history. A user who wants only reviewed problems filters on the issue column.

## v1 scope

The table site over the deterministic producers for every eval, review files applied, accepted issues linked to GitHub, Inputs and freshness per eval, investigator results shown where they exist (SciCode, chess). Blog posts continue and start importing exports. The measurement runs beside it.

In order: the findings repo and its scheduled run; the export; the site; the import of the two existing investigations. The measurement slice follows.

## Open decisions

- Repository name and who has write access to the findings repo. Proposed: `Generality-Labs/inspect-evals-findings`, Matt and Tania.
- Schedule: nightly or weekly. Nightly costs little (lint and dataset scans are minutes per eval) and keeps "last successful run" fresh; start nightly, drop to weekly if Hawk pulls dominate.
- Site domain: `audits.generality.org` or a path on `generality.org`. A subdomain keeps the deploy independent of the Quarto site.
- Whether the export is public (the site reads it directly) or the site is built from it privately and only pages are public. Start with pages only; the parquet becomes public when the standard does.
- Security review of the publication path before the site is public, as the roadmap already requires.
