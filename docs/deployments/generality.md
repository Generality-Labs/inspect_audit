# Generality Labs' deployment

The facts about Generality Labs' own instance of the findings store: where it lives, what reads it, and which credentials it uses. None of this is in the package; another organisation writes its own version of this page. The product is described in [../architecture.md](../architecture.md).

## The store

- **Location.** R2 bucket `inspect-evals-findings` in the Generality Labs Cloudflare account (`4dd4a6b03a79f452ebc112658643f02a`), created 2026-10-12 with a Western Europe location hint and no jurisdiction. Locator `s3://inspect-evals-findings`, endpoint `https://4dd4a6b03a79f452ebc112658643f02a.r2.cloudflarestorage.com`. R2 has no bucket versioning; the store does not need it.
- **Contents.** The runs of the 2026-10-08 registry sweep (120 evals), copied in from the old git store on 2026-10-12, and everything rendered from them.
- **Credentials.** An R2 API token named `inspect-evals-findings-R2`, Object Read & Write on that bucket only, expiring 13 October 2027. Its Access Key ID and Secret Access Key live in a password manager and, for running sweeps, in a gitignored `.env` at the root of the checkout the CLI runs from, which `inspect-audit-findings` loads itself. That file also sets `AWS_ENDPOINT_URL`, `AWS_DEFAULT_REGION=auto` (needed because `~/.aws/config` there defaults to `eu-west-2`, which R2 rejects) and `INSPECT_AUDIT_STORE=s3://inspect-evals-findings`. Never exported in the shell: `AWS_ENDPOINT_URL` would redirect every S3 client there, including evals reading data from AWS.

## The deployment repository

`Generality-Labs/inspect-evals-findings` (private) holds the configuration for this store rather than the store itself: `audit.toml`, the Hawk artefact manifest `hawk-artefacts.yaml`, and a README on running sweeps. Its git history keeps the pre-R2 copies of the runs. `audit.toml` is also copied to the bucket root, where the CLI reads it.

## The site

- `audits.generality.org`, from `Generality-Labs/audits-site`, a Worker scaffolded from `Generality-Labs/cloudflare-worker-template`. It serves a Vite-built React app and reads `export/` from the bucket through its `FINDINGS` R2 binding; no token. Deploys run from CI on push to main; the CI token needs only Workers Editor (bindings need no separate permission) and Workers Routes on `generality.org`.
- Behind Cloudflare Access: anyone with a `@generality.org` email, by one-time PIN; `/health` bypasses for the deploy smoke test. Going public is removing the policy, after the security review of the publication path. Cloudflare disables the Cache API behind Access, so while the gate is on every `/data` request reads the bucket.

## Logs and investigations

- Logs on Generality's Hawk deployment are read by `--hawk-task` or `--logs hawk:<eval-set-id>` after `hawk login`; `hawk-pull --manifest hawk-artefacts.yaml` from the deployment repository fetches the SciCode, HLE physics and chess artefacts into a gitignored `artefacts/` directory.
- Investigations run on Hawk and publish bundles to its S3; importing them into the store is milestone 5 on the [roadmap](../roadmap.md).

## Issues and reports

- Accepted issues link to GitHub issues on `UKGovernmentBEIS/inspect_evals`. When `promote` lands, `[tracker] kind = "github"` with that repository goes in `audit.toml`.
- Blog posts on `generality.org/blog` are human-written Quarto documents that import the JSON they need from the export into their `data/` directories, as the BixBench post does.

## Building audits-site

Moved from the table site spec, 2026-10-12. Kept for whoever extends the site.

**The export shapes.** `src/inspect_audit/findings/export.py` is the source of truth; real files carry more than the spec's examples: `inputs.logs.count_excluded`; dataset keys such as `config`, `split`, `revision`, `task` and `inspect_ai`; `quote` and `end_line` as null on code locations; `runs[*].passing`; `runs[*].assessments`, each with its findings as record ids and its measure's derived `value`; `issues[*].reason`; `suppressed[*].producer` may be null. The site's fixture is a real export plus a few additions.

**Cloudflare.** The custom domain is a route under `[env.production]` on the `generality.org` zone, declared the way `llm-prices.generality.org` is. Repo secrets the template's deploy needs: `CLOUDFLARE_API_TOKEN` and `CLOUDFLARE_ACCOUNT_ID`; a `HEALTH_URL` variable on the GitHub environment for the smoke test. The Worker has no secrets since the move to the R2 binding (audits-site #7).

**The template.** Private, Copier; the scaffold's CI and deploy call its shared `worker-ci` and `worker-deploy` workflows at `@v1`. The top level of `wrangler.toml` is local only and every deploy targets a named environment; actions are SHA-pinned and zizmor runs in pre-commit; Biome formats TypeScript. vitest has two projects, `unit` in Node (the app's jsdom tests) and `worker` in workerd. Node 26.

**In-house reference.** `Generality-Labs/llm-prices` is a Worker on this account with `[assets]`, a `fetch` handler and a remote MCP endpoint: the reference for MCP wiring when the hosted variant is built.

**Brand.** Colours from inspect_evals `docs/_brand.yml`; Geist bundled from `@fontsource-variable/geist`; no third-party requests.

**Conventions.** UI pull requests carry before-and-after screenshots taken with Playwright. Each slice is a plan, executed with tests first, then a fresh reviewer on the whole branch.
