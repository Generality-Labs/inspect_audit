# Table site design

Written 2026-10-07 after the brainstorm with Matt. This is the design for the first deploy of the findings table site described in [v1-architecture.md](../../v1-architecture.md) ("The table site") and for the export that feeds it. Two repositories are involved: `inspect_audit` gains the export; a new `Generality-Labs/audits-site` holds the Worker and the React app. The export ships first, so the site is built against real data.

## Purpose

One filterable, sortable table of every active finding across every eval, and a page per eval that says what was checked, when, what was skipped and why, what is suppressed and what has been accepted. Everything active is shown with provenance as columns, not gated behind the investigator. A page with no findings must be distinguishable from a page nobody checked. Read-only in this slice: review actions and the MCP endpoint come later, and the Worker is shaped so they can be added without restructuring.

## Decisions

| Question                                | Decision                                                                                                                                                                                          | Why                                                                                                                              |
| --------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------- |
| Where the export is materialised        | Written by `render_current` into `export/` in the findings repo, committed with every sweep and every review decision                                                                             | One function on a step that already runs on every write; cannot drift from the summaries; one place decides what leaves the repo |
| How the site gets data                  | The Worker fetches `export/*.json` from the private findings repo over `raw.githubusercontent.com` with a read token and caches at the edge                                                       | No data in the site repo, no Cloudflare credentials in the findings repo, no second workflow                                     |
| Cache                                   | The Cache API (`caches.default`), five-minute TTL                                                                                                                                                 | Zero configuration; KV is the move if GitHub availability or cold fetches ever matter                                            |
| Rendering                               | Vite + React 19 client app served as Worker static assets; no server rendering                                                                                                                    | The table is client state; the Worker stays a few routes                                                                         |
| Router                                  | Plain `fetch` handler, no framework                                                                                                                                                               | Three branches do not need Hono; the MCP SDK brings its own handler later                                                        |
| Access                                  | Cloudflare Access on `audits.generality.org` for Generality Labs Google accounts                                                                                                                  | Pilot data about named evals stays internal until reviewed and the security review is done; going public is deleting the policy  |
| Staging                                 | None in this slice                                                                                                                                                                                | Production is gated, so it is the staging site; add via `copier update` with `use_staging` when the policy comes off             |
| Pages                                   | Findings table and eval page                                                                                                                                                                      | The evals overview waits until there are more than a handful of evals                                                            |
| Hosting of the Store API and MCP server | Store API: Python, run locally and in the findings repo's Actions; hosted writes arrive as `repository_dispatch`. MCP: Python stdio locally; hosted read tools in this Worker, writes by dispatch | Recorded in v1-architecture.md with this spec                                                                                    |

## The export

### What `render_current` writes

Alongside the summaries and parquet, from the same current view with review applied:

```
export/index.json
export/evals/<slug>.json
```

The sweep runner commits `export/` with the sweep, by hand for now and by the nightly Action later. Review decisions re-render through the Store, so they commit it too.

### Publication boundary

Suppressed observations are counted in the index and listed as groups on the eval page; they never appear as rows. The producers' native `source` records are not exported. Everything else in the envelope that the pages show is exported as is. This is the one place that decides what leaves the repo.

### `index.json`

```json
{
  "generated_at": "2026-10-07T04:20:50Z",
  "schema": 1,
  "evals": [
    {
      "eval": "inspect_evals/stereoset",
      "slug": "inspect-evals-stereoset",
      "revision": {"commit": "dbd3dd25e", "package_version": "0.22.0", "dirty": false},
      "task_version": "3-A",
      "last_run": "2026-10-06T04:24:55Z",
      "producers": {
        "inspect_evals_lint": {"run_id": "inspect_evals_lint-20261006T042442Z-inspect-evals-stereoset", "timestamp": "2026-10-06T04:24:42Z", "skipped": null},
        "inspect_dataset": {"run_id": "...", "timestamp": "...", "skipped": null},
        "inspect_audit_header": {"run_id": "...", "timestamp": "...", "skipped": "no logs matched the declared filter"}
      },
      "active": 1,
      "suppressed": 0,
      "issues": 1
    }
  ],
  "findings": [
    {
      "id": "inspect_evals_lint-20261006T042442Z-inspect-evals-stereoset/1",
      "fingerprint": "sha256:90a0…",
      "eval": "inspect_evals/stereoset",
      "slug": "inspect-evals-stereoset",
      "producer": "inspect_evals_lint",
      "rule": "IEBP011",
      "dimension": "environment",
      "check": null,
      "severity": "minor",
      "status": "supported",
      "summary": "shuffle defaults to True, and hf_dataset() on line 55 shuffles the samples without a seed",
      "location": "code:src/inspect_evals/stereoset/stereoset.py:46",
      "issue": "ISS-0001",
      "github": "https://github.com/UKGovernmentBEIS/inspect_evals/issues/0",
      "first_seen": "2026-10-05T11:24:42Z",
      "last_seen": "2026-10-06T04:24:42Z"
    }
  ]
}
```

`producers[*].skipped` is the skip reason when the run was a skip, else null. `last_run` is the newest run timestamp across producers. `first_seen` and `last_seen` are the earliest and latest run timestamps, across the whole run history (`read_runs`), of any run containing the fingerprint; both are on the eval page as well. `github` is the linked issue's URL when the issue has one. `findings` holds active rows only, every eval, sorted by eval then rule then id.

### `evals/<slug>.json`

```json
{
  "generated_at": "2026-10-07T04:20:50Z",
  "schema": 1,
  "eval": "inspect_evals/stereoset",
  "slug": "inspect-evals-stereoset",
  "revision": {"commit": "dbd3dd25e", "package_version": "0.22.0", "dirty": false},
  "task_version": "3-A",
  "inputs": {
    "dataset": {"path": "McGill-NLP/stereoset", "mode": "task", "declared": true, "samples": 2123, "scorers": ["inspect_evals/multiple_choice_scorer", "inspect_evals/stereoset_scorer"]},
    "logs": {"used": [{"path": "hawk:…", "model": "…"}], "excluded": [{"path": "…", "reason": "mock model"}]},
    "comparison": {"revision": "dbd3dd25e"}
  },
  "runs": [
    {"run_id": "…", "producer": "inspect_dataset", "producer_version": "0.5.0", "timestamp": "…", "duration_s": 61.2, "skipped": null,
     "outcomes": [{"rule": "answer_length", "status": "skip", "message": "answer_length assumes a scorer that compares answer text verbatim; this task scores with …"}]}
  ],
  "groups": [
    {"producer": "inspect_evals_lint", "rule": "IEBP011", "dimension": "environment", "check": null, "severity": "minor", "count": 1,
     "findings": [{"id": "…", "fingerprint": "…", "status": "supported", "summary": "…", "locations": [{"kind": "code", "role": "primary", "file": "src/inspect_evals/stereoset/stereoset.py", "line": 46, "column": 15}], "issue": "ISS-0001", "first_seen": "…", "last_seen": "…"}]}
  ],
  "suppressed": [
    {"producer": "inspect_dataset", "rule": "inconsistent_format", "count": 0, "kind": "false_positive", "author": "Matt Fisher", "reason": "length outliers on struct answers", "since": "2026-10-05"}
  ],
  "issues": [
    {"id": "ISS-0001", "title": "StereoSet shuffles without a seed", "author": "Matt Fisher", "opened": "2026-10-05", "reason": "…", "github": "https://…", "current": 1}
  ]
}
```

`inputs` is the structured form of what `render.inputs_lines` prints today; the renderer gains a data-returning function that both the Markdown and the export use, so the two cannot disagree. `outcomes` lists only non-pass outcomes, as the summary table does, with a `passing` count beside them. `groups` carries every active finding with its full `locations`. `suppressed` has one row per suppression rule that applies to the eval, with the number of observations it matched in the current view. The author shown is the name part of `Name <email>`; emails are not exported. `issues[*].current` is the number of current observations linked to the issue.

### Versioning

`schema` is 1. A field removed or renamed bumps it; a field added does not. The app checks `schema` and shows a plain message when it is newer than it understands.

## The Worker

One `fetch(request, env)` handler in `src/index.ts`:

- `GET /healthz` returns `200 ok`. The template's deploy workflow smoke-tests it.
- `GET /data/index.json` and `GET /data/evals/<slug>.json`, where `<slug>` matches `^[a-z0-9-]+$`. The handler builds `https://raw.githubusercontent.com/<FINDINGS_REPO>/<FINDINGS_REF>/export/<path>`, looks the URL up in `caches.default`, and on a miss fetches upstream with `Authorization: Bearer <GITHUB_TOKEN>`, stores the response with `Cache-Control: public, max-age=300`, and returns it with `Content-Type: application/json`. Upstream 404 becomes 404; any other upstream failure becomes 502 with a one-line body. Nothing from the upstream response headers is forwarded, so the token cannot leak.
- Any other `/data/*` path is 404.
- Everything else never reaches the handler: the assets layer serves `dist/`, with `not_found_handling = "single-page-application"` so deep links such as `/evals/inspect-evals-stereoset` return `index.html` and React routes them.

Config in `wrangler.toml`: `[assets] directory = "./dist"`, `binding = "ASSETS"`; `[vars] FINDINGS_REPO = "Generality-Labs/inspect-evals-findings"`, `FINDINGS_REF = "main"`; secret `GITHUB_TOKEN`, a fine-grained personal access token with Contents read on the findings repo only, put with the template's `scripts/put-secrets.sh`. The token expires within a year; its renewal date goes in the README.

Access is a Cloudflare Access application on `audits.generality.org`, policy "Generality Labs Google accounts", created once in the Zero Trust dashboard. The README records that it exists and that removing it is how the site goes public. `/data/*` and the future `/mcp` sit behind the same gate; a script or sandbox needs an Access service token until then.

## The React app

Vite, React 19, TypeScript strict, React Router, TanStack Table (headless), plain CSS in one stylesheet. No component library, no state library. Source under `app/`, built to `dist/`.

Routes:

- `/`: the findings table. Loads `/data/index.json` once. Columns: eval (link to the eval page), producer, rule, dimension, check, severity, status, summary, location, reviewed (issue id linking to GitHub when `github` is set, else blank), first seen, last seen. Filters: eval (text), producer, dimension, check, severity, status, reviewed (any, accepted, unreviewed), all combinable; the active filter set is reflected in the query string so a view can be linked. Sort on any column; default sort is reviewed rows first, then by eval and rule. A count line reads "N of M findings" and the export's `generated_at` is shown in the header as "data from <time>".
- `/evals/<slug>`: the eval page. Loads `/data/evals/<slug>.json`. Sections in order: header (eval, revision, task version, last run per producer with skipped reasons in red), Inputs (dataset scanned, scorers assumed, logs used and excluded with reasons, comparison revision), Findings grouped by rule with the group's count and each finding's summary, location and status, Suppressed (rule, count, kind, reason, author, since), Issues (id, title, current observations, GitHub link). An eval whose runs all passed shows "No findings. N checks ran, M skipped." so an empty page is never mistaken for an unchecked one. An unknown slug shows a not-found message with a link back.
- A schema newer than the app understands shows "This site needs updating to read the current export." and nothing else.

No pagination: the index is held in memory and TanStack Table filters it. If row counts ever make this slow, virtualised rows are the first move and DuckDB-WASM over parquet the second.

## Repository, CI, deploy

`Generality-Labs/audits-site`, private, scaffolded with `uvx copier copy gh:Generality-Labs/cloudflare-worker-template` and these answers: no staging, no D1, no R2, no KV, no cron, Playwright yes, typos no, template-update yes, Node as the template defaults.

Additions to the scaffold: Vite and React dependencies; `app/` with its own `tsconfig.app.json` targeting the DOM; `vite.config.ts`; the npm scripts `build` (`vite build`), `dev:app` (`vite`), and `deploy` changed to `vite build && wrangler deploy --env production`; `typecheck` extended to run `tsc --noEmit -p tsconfig.app.json` as well. The template's shared CI runs install, typecheck, tests and pre-commit unchanged; the deploy workflow runs the `deploy` script on push to main, then smoke-tests `/healthz` on `https://audits.generality.org`. `dist/` is gitignored.

Production route: `audits.generality.org` as a custom domain under `[env.production]`. Secrets `CLOUDFLARE_API_TOKEN` and `CLOUDFLARE_ACCOUNT_ID` on the repo, as the template requires.

## Testing

- Worker, in workerd through the template's vitest pool, with the upstream GitHub fetch stubbed via `fetchMock` from `cloudflare:test`: a miss fetches once and stores; a second request within the TTL does not fetch; a slug outside the allowed pattern is 404 without an upstream call; an upstream 404 is 404; an upstream 500 is 502; the token is in the upstream request and in no response header; `/healthz` is 200.
- App, in Node with jsdom and Testing Library, over a fixture export under `test/fixtures/export/` (two evals, a dozen findings, one suppression, one issue): each filter narrows the rows; sort flips; the query string round-trips; the eval page renders each section; the empty-but-checked state; the unknown slug; the newer-schema message.
- Playwright against `wrangler dev` serving the built app with the fixture export in place of GitHub (the dev `FINDINGS_REPO` points at a local stub route): one flow through the table to an eval page, and the screenshots for pull requests.
- The export, in inspect_audit's suite: `render_current` writes both files; the index excludes suppressed rows and counts them; `source` is absent everywhere; first and last seen come from the history, not the current view; the eval file's `inputs` equals what the Markdown Inputs section says; `schema` is 1.

## Out of scope for this slice

Review actions on the site; the MCP endpoint; the evals overview page; KV or R2; staging; a public site; Assessment and Coverage sections for investigated evals (SciCode, chess), which arrive with the investigation import; the nightly Action.

## Open items

- Repository name `audits-site` and who else gets write access. Proposed: Matt and Tania.
- Whether the export's `github` URLs should be shown while the site is gated. Proposed: yes; they are public issues.
