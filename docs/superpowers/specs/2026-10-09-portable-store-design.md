# Portable store design

Written 2026-10-09. Other organisations are going to run inspect_audit, so the package must not assume Generality Labs' repositories, hosts or accounts. This spec draws the line between the product and a deployment, moves the store from a git repository to object storage behind a filesystem abstraction, and turns review decisions into an append-only log so that object storage needs no locking. Generality's own instance becomes one documented reference deployment. It supersedes "The store" and the hosting paragraphs of [v1-architecture.md](../../v1-architecture.md); the rest of that document stands until the doc split below.

## Principle

inspect_audit defines three things: the store format (runs, decisions, export), the review semantics (what a suppression, an acceptance, a link and a status change mean and how they fold into a view), and the CLI that reads and writes them. Everything about where a store lives, who may write to it, how the export is published, which issue tracker accepted findings go to, where logs come from and what hosts the MCP server is deployment configuration. The package ships no default that names an organisation, a repository, a bucket or a domain.

## Decisions

| Question                       | Decision                                                                                                                                                                               | Why                                                                                                                                                        |
| ------------------------------ | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Where the store lives          | Any fsspec location: a local directory, `s3://`, R2 through its S3 endpoint, or another fsspec backend; named by a locator string                                                      | Inspect writes logs to `s3://` through fsspec and Hawk's bundles live in S3; a sandbox that can read its logs can read the store with the same credentials |
| Filesystem library             | fsspec with s3fs, both already dependencies of inspect_ai                                                                                                                              | No new dependency; the same credential conventions as an Inspect log dir                                                                                   |
| Concurrency model              | Runs are immutable objects; decisions are an append-only log of objects; every other file is derived and may be rewritten by any writer                                                | Nothing shared is ever edited in place, so no locks and no merge conflicts                                                                                 |
| Review files                   | `suppressions.yaml` and `issues.yaml` become derived views folded from the decision log; nothing writes them directly                                                                  | The log carries author, time and reason itself, which is what git blame was for                                                                            |
| Approval of decisions          | Direct writes by whoever holds credentials; no pull requests                                                                                                                           | What we do in practice; gated approval, if ever wanted, is a status on a decision record, not a transport                                                  |
| Git commit in the Store        | Removed                                                                                                                                                                                | The store is no longer a repository                                                                                                                        |
| Configuration                  | `audit.toml` at the store root (and optionally beside a checkout) names the eval profile, the tracker and the publish target; the locator is a CLI argument or an environment variable | Deployment facts live with the deployment                                                                                                                  |
| Publishing                     | The export is written into the store under `export/`; what serves it is the deployment's business                                                                                      | A static app that reads `/data/` can be hosted beside any export                                                                                           |
| MCP                            | A Python server over stdio and a store locator ships with the package; hosted variants are deployment                                                                                  | The read tools are queries over the store; where they run is not the product's concern                                                                     |
| Issue tracker                  | A configured target; `promote` exists only when one is configured, otherwise `link` records a URL                                                                                      | Not every organisation files issues on GitHub, or anywhere                                                                                                 |
| Logs on Hawk                   | `hawk:` stays as one log-source scheme; the artefact manifest moves out of the package                                                                                                 | Hawk is a tool anyone may run; our manifest is ours                                                                                                        |
| Taxonomy name                  | Open: rename `gl-audit` to a neutral name before the next sweep (see Open items)                                                                                                       | The name is in every record; a rename later needs a mapping and a version bump in every organisation's data                                                |
| Eval profile (registry layout) | Out of scope here; `audit.toml` reserves `profile = "inspect_evals"` for it                                                                                                            | The real portability work for targets outside the Inspect Evals registry needs its own design                                                              |

## The store

### Locator

A store is named by one string, resolved by fsspec: `/path/to/store`, `file:///…`, `s3://bucket/prefix`, or `s3://bucket/prefix` with `AWS_ENDPOINT_URL` set for R2 and other S3-compatible services. Credentials come from the environment the same way Inspect's `--log-dir s3://…` finds them: `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_ENDPOINT_URL`, or a profile. The CLI takes `--store LOCATOR`; `INSPECT_AUDIT_STORE` is the default when the flag is absent. `--out` remains as the name of the same argument for `run` and `summary` for one release, then goes.

Bucket versioning is recommended for rollback; the package does not depend on it.

### Layout

Unchanged for runs and export, new for decisions:

```
audit.toml                       deployment configuration (see below)
<slug>/runs/<run id>.run.json    immutable producer runs; newest per producer is current
review/<at>-<id>.json            one decision each, append-only
suppressions.yaml                derived from review/, for reading only
issues.yaml                      derived from review/, for reading only
<slug>/SUMMARY.md, SUMMARY.md    derived
findings.parquet, runs.parquet   derived
export/index.json                derived, what a site reads
export/evals/<slug>.json         derived
cases.yaml                       the labelled defects for measurement; written by hand, rarely
```

Derived files are convenience. A reader that cannot trust them re-renders from runs and decisions. Any writer may rewrite them after its own write; the last render wins, and a stale render is repaired by the next. This is the same rule that already applies to the parquet.

### Backend in the code

`io.py` gains a `StoreFS` wrapper around an fsspec filesystem and a root: `ls(prefix)`, `read_text(path)`, `write_text(path, text)`, `write_bytes`, `exists`, `glob(pattern)`. `read_current`, `read_runs`, `write_run`, the review reader and writer, the renderer's file writes and `write_export` all take a `StoreFS` instead of a `Path`. The local backend is the one tests use, through fsspec's `file` or `memory` filesystem, so no test needs network. One integration test against R2 runs only when credentials are present.

Listing: the derived current view lists `<slug>/runs/` for every eval on every render. At the registry's size (about 130 evals, three producers, one run each per night) that is a few hundred objects per render per day of history. If it ever matters, a per-eval `index.json` of run ids can be derived too; not now.

## Decisions as an append-only log

### Record

```json
{
  "id": "dec-20261009T041210Z-7f3a",
  "at": "2026-10-09T04:12:10Z",
  "author": "Matt Fisher <matt@generality.org>",
  "kind": "suppress",
  "reason": "struct-typed answers; inspect_dataset#26",
  "suppress": {"rule": "answer_length", "subject": "inspect_evals/stereoset", "producer": "inspect_dataset", "kind": "false_positive"}
}
```

`kind` is one of `suppress`, `accept`, `link`, `status`, `retract`. Exactly one payload field, named after the kind, is present:

- `suppress`: `rule`, `subject` (`"*"` or an eval), `producer` or null, `kind` (free text as today).
- `accept`: `issue` (an id the writer assigned, `ISS-NNNN` by the CLI), `title`, `subject`, `fingerprints` (one or more).
- `link`: `issue`, `url`.
- `status`: `fingerprints`, `status` (hypothesis, supported, qualified, retracted).
- `retract`: `decision` (the id of an earlier decision this one withdraws), so a mistaken suppression or acceptance can be undone without deleting history.

Object key: `review/<at>-<id>.json`, so a listing is already in time order. Authors are stored in full (`Name <email>`) and exported as names only, as the export already does.

### Fold

`fold(decisions) -> Review` applies the log in key order: suppressions accumulate; an acceptance creates an issue; a link sets its URL (the latest wins); a status sets the status of each fingerprint (the latest wins); a retraction removes the effect of the decision it names. The result is the `Review` the renderer applies today, so `apply_review`, the leads, the summaries and the export do not change. `suppressions.yaml` and `issues.yaml` are written from the fold on every render as read-only views; a `# derived from review/; do not edit` header is their first line.

Validation happens at write time against the current fold: an acceptance of a fingerprint another issue owns is refused; a link to an unknown issue is refused; a status on a fingerprint not in the current view is refused; a retraction of an unknown decision is refused. Two writers racing on the same fingerprint can still both succeed, because object storage gives no transaction. The fold then applies both in time order and the later one wins; the renderer prints a warning naming the two decisions. This is accepted: it is rare, visible and reversible by a retraction.

### Migration

The existing `suppressions.yaml` and `issues.yaml` in the findings repo become decisions with their recorded author and date, one object each, through a one-off `review migrate` command that is removed in the following release.

## The Store API

`Store(locator, config=None)` replaces `Store(root, review_dir)`. Reading: `runs()`, `history()`, `review()` (the fold), `reviewed()`, `resolve(selection)`. Writing: `suppress`, `accept`, `link`, `set_status`, `retract`, each validating against the fold, putting one decision object, and re-rendering. No git. The `review` CLI gains `status` and `retract` verbs and `--store`. `promote` appears when `audit.toml` configures a tracker.

Authorship: `--author`, else `INSPECT_AUDIT_AUTHOR`, else git's `user.name` and `user.email` from the current directory if any, else a usage error. The same rule as today without the git-repo requirement.

## `audit.toml`

At the store root, read on every command; absent means every default below.

```toml
[store]
# locator is not stored here; it comes from --store or INSPECT_AUDIT_STORE

[evals]
profile = "inspect_evals"           # how targets are resolved; the only profile today
root = "../inspect_evals"           # default --root for run

[tracker]
kind = "github"                     # omit the table to disable promote
repo = "UKGovernmentBEIS/inspect_evals"

[publish]
# nothing yet; the export is written into the store. A deployment that copies it
# elsewhere does so outside the package.
```

The package ships none of these values. `pilot.yaml` stays as the per-eval scan declarations for the `inspect_evals` profile; its default location becomes `<store>/pilot.yaml` when present, else the packaged one, so an organisation can declare its own evals without forking.

## Publishing and the site

The export is objects under `<store>/export/`. The site (`audits-site`) reads an export origin, in one of two modes:

- A plain HTTPS origin: `FINDINGS_ORIGIN = "https://…/export"` and the Worker fetches `<origin>/index.json` and `<origin>/evals/<slug>.json`. This covers a public R2 bucket with a custom domain, an S3 bucket behind CloudFront, GitHub Pages, or a static host that has the export copied beside the app.
- An R2 binding, for a private bucket on the same Cloudflare account: `[[r2_buckets]] binding = "FINDINGS"` and the Worker reads objects directly. No token, and the Cache API works once the site is public.

The GitHub contents-API mode is removed once Generality's store moves to R2. The app itself does not change: it reads `/data/*` from its own origin.

## MCP

`inspect-audit-findings mcp --store LOCATOR` serves the read tools (`leads`, `finding`, `search`, `issues`, `inputs`) and the write tools (`suppress`, `accept`, `set_status`) over stdio, through the `Store`. Provenance for write tools is the agent's run id, passed as the author. Anything hosted, such as an HTTP transport in a Worker or a sidecar in a Hawk sandbox, is a deployment that wraps this or re-implements the read tools over the export; it is not in the package.

## Logs

`collect_logs` resolves log sources by scheme: a local path, `hawk:<eval-set-id>`, and `s3://…` through fsspec (new, so an organisation whose logs sit in a bucket needs no Hawk). `--hawk-task` stays as a Hawk convenience. `scripts/hawk-artefacts.yaml` and `hawk-pull` move to the Generality deployment repository; the Hawk client code stays in the package as the `hawk:` scheme.

## Generality's deployment

Documented in `docs/deployments/generality.md`, not in the architecture:

- Store: an R2 bucket in the Generality Labs Cloudflare account, locator `s3://inspect-evals-findings` with the account's S3 endpoint; versioning on. Credentials: an R2 API token scoped to that bucket, in the nightly workflow's secrets and in operators' environments.
- `Generality-Labs/inspect-evals-findings` becomes the deployment repository: `audit.toml`, `pilot.yaml`, `cases.yaml`, the Hawk artefact manifest, and the nightly workflow that checks out Inspect Evals, runs the producers with `--store` pointing at the bucket, and exits. Its git history stops carrying runs.
- Site: `audits-site` reads the bucket through an R2 binding; Access stays until the security review.
- Tracker: `github` on `UKGovernmentBEIS/inspect_evals`.
- Logs: Hawk, by `--hawk-task`.

## Doc split

`v1-architecture.md` is rewritten as `architecture.md`: the product only, with the store section replaced by a pointer to this spec, and no organisation names. The deployment facts (account id, bucket, domain, Access, secrets, tracker, Hawk manifest) move to `docs/deployments/generality.md`, including the implementation notes section of the table site spec. The roadmap's milestone 3 and 4 entries are reworded to the product terms with a "Generality" note where our instance differs. The findings-store proposal and standard drafts in the private artefacts are unaffected.

## Migration plan

Each step leaves the system working.

1. `StoreFS` over fsspec with the local directory as the only backend used; `--store` accepted as a synonym for `--out`. No behaviour change.
2. The decision log and the fold; `review migrate` converts the two YAML files; the YAML files become derived. The Store loses its git commit.
3. The R2 bucket and token; one sweep writes to `s3://…`; the nightly workflow, when it lands, points there.
4. The site's R2 binding mode; the contents-API mode removed.
5. `audit.toml`, the tracker configuration behind `promote`, the `s3://` log scheme, the Hawk manifest moved to the deployment repository.
6. The doc split and the findings repository's history trimmed to configuration (a fresh repository or a rewrite; either is fine, the runs are in the bucket).

Steps 1 and 2 are one slice; 3 and 4 another; 5 and 6 a third.

## Testing

- `StoreFS` on fsspec's `memory` filesystem: round-trips, listing order, glob; one test against R2 behind an environment guard.
- The fold: each decision kind, the latest-wins rules, retraction, the race warning, and `migrate` on the current YAML files reproducing today's `Review` exactly.
- The Store: every write refuses what the fold refuses; a write puts exactly one object and re-renders; no git repository is created or required.
- The CLI: `--store` on every subcommand, `INSPECT_AUDIT_STORE` default, the author rule without git.
- The export and summaries: byte-identical output for the same store on a local directory and on the memory filesystem.

## Out of scope

The eval profile for targets outside the Inspect Evals registry; a hosted MCP transport; gated approval of decisions; copying the export anywhere; the nightly workflow itself (it changes only its destination).

## Open items

- Taxonomy name: `gl-audit@1` to `inspect-audit@1` (or similar) now, as a one-sweep rename, or keep the name. Recommended: rename before the next registry sweep.
- Whether derived `suppressions.yaml` and `issues.yaml` are worth keeping once nothing reads them but people. Recommended: keep for one release, then decide.
- The bucket's name and whether one bucket holds the store and the export or the export gets a public bucket of its own.
