# Deployment configuration and doc split Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Slice three of the portable store spec, minus the tracker and the nightly workflow. A store carries its own deployment configuration (`audit.toml`, `pilot.yaml`); logs can come from any fsspec location, not only Hawk; the package names no organisation's files; the docs separate the product from Generality's deployment; and the git findings repo becomes the deployment repository.

**Architecture:** `findings/deployment.py` reads `audit.toml` and the store's `pilot.yaml` through `StoreFS`. `run` takes `--root` from `[evals] root` and the per-eval declarations from the store when the flags are absent. `collect_logs` downloads any `scheme://` log source other than `hawk:` and `file://` into a cache, as `hawk:` already does, so producers keep reading local paths. `hawk-pull` needs an explicit `--manifest`; Generality's manifest moves to the findings repo. `docs/architecture.md` replaces `v1-architecture.md` with product-only text; `docs/deployments/generality.md` holds the account, bucket, domain, Access, credentials and Hawk facts.

**Tech Stack:** Python 3.13 (`tomllib`), pydantic v2, fsspec via inspect_ai, pytest with `memory://`. Gate: `uv run pre-commit run --all-files`, `uv run basedpyright src`, `uv run pytest -q -p no:cacheprovider`.

**Spec:** `docs/superpowers/specs/2026-10-09-portable-store-design.md`: "`audit.toml`", "Logs", "Generality's deployment", "Doc split", migration steps 5 and 6.

## Global Constraints

- No new dependency; `tomllib` is in the standard library from 3.11.
- The package ships no value that names an organisation, repository, bucket or domain. `audit.toml` absent means every default.
- Precedence for each setting: command-line flag, then the store's file, then the packaged default.
- Out of scope, by decision: the `[tracker]` table (it only matters for `promote`, deferred), and the nightly workflow.
- Commit messages end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`. Stage new files before the gate.

## Review Focus

1. A malformed `audit.toml` or store `pilot.yaml` must be exit 2 naming the file, before any producer runs.
2. `--root` given on the command line must win over `[evals] root`; a relative `[evals] root` resolves against the current directory, not the store (the store may be remote).
3. A remote log source that lists no logs must be a clear error naming it, not a silent sweep with no header checks.
4. A second sweep with the same remote log source must not download the logs again.
5. No file in `src/` or `scripts/` names Generality, its account, bucket or domain after this branch.

______________________________________________________________________

### Task 1: `audit.toml` and the store's `pilot.yaml`

**Files:** Create `src/inspect_audit/findings/deployment.py`; modify `config.py` (`load_config_text`), `cli.py` (`run`: `--root` and `--config` defaults); tests `tests/findings/test_deployment.py`, `tests/findings/test_cli.py`.

**Interfaces:**

- `DEPLOYMENT_FILE = "audit.toml"`, `PILOT_FILE = "pilot.yaml"`.
- `class EvalsSection(BaseModel)`: `profile: Literal["inspect_evals"] = "inspect_evals"`, `root: str | None = None`. `class Deployment(BaseModel)`: `evals: EvalsSection = EvalsSection()`. Both `extra="forbid"`.
- `load_deployment(fs: StoreFS) -> Deployment`: absent file is the default; malformed TOML or an unknown key raises `ValueError` naming `audit.toml`.
- `store_pilot(fs: StoreFS) -> Config | None`: the store's `pilot.yaml` parsed with `load_config_text`, or `None` when absent; errors name the file.
- `config.load_config_text(text: str, source: str) -> Config`; `load_config(path)` calls it.
- CLI `run`: `--root` no longer required; `--config` default `None`. Root: flag, else `[evals] root` resolved against the current directory, else exit 2 saying so. Config: flag, else the store's `pilot.yaml`, else the packaged default.

**Tests (write first, watch fail):**

- `test_deployment.py`: defaults when absent; `[evals] root` read; unknown profile and unknown key raise naming `audit.toml`; malformed TOML raises naming it; `store_pilot` returns `None` when absent and a `Config` when present; a malformed store `pilot.yaml` raises naming it.
- `test_cli.py`: `run` with no `--root` and `audit.toml` setting `[evals] root` sweeps that root; `--root` beats `audit.toml`; no `--root` and no `audit.toml` exits 2 naming both ways; the store's `pilot.yaml` is used when `--config` is absent (assert through a declared dataset field reaching `inputs.dataset`); a malformed `audit.toml` exits 2 naming it and runs nothing.

### Task 2: logs from any fsspec location

**Files:** Modify `cli.py` (`collect_logs`, `--log-cache`); tests in `test_cli.py`.

**Interfaces:**

- `REMOTE_LOG_CACHE = Path.home() / ".cache" / "inspect_audit" / "logs"`.
- `collect_logs(sources, *, hawk_cache=..., remote_cache=REMOTE_LOG_CACHE, producers=None)`: a source with `://` other than `file://` is fetched by `_fetch_remote_logs(url, cache)` into `cache / <first 12 hex of sha1(url)>`, keeping each file's path relative to the URL; a file already present with the same size is not fetched again; a URL with no `.eval` or `.json` logs raises `ProducerError` naming it.
- `run` gains `--log-cache` (default `REMOTE_LOG_CACHE`).

**Tests:** logs written to `memory://logs-a/x/` are fetched and returned as local paths; a second call fetches nothing (count `get` calls); an empty remote prefix raises naming it; `run --logs memory://…` exits 2 with the message when empty.

### Task 3: no organisation files in the package

**Files:** Modify `cli.py` (`hawk-pull --manifest` required); delete `scripts/hawk-artefacts.yaml`; `.gitignore` comment; test `test_cli.py`.

**Tests:** `hawk-pull` without `--manifest` exits 2 (argparse); the existing manifest test keeps passing. A test greps `src/` and `scripts/` for `generality`, `4dd4a6b0`, `inspect-evals-findings` and `audits.generality` (case-insensitive) and expects none.

### Task 4: the doc split

**Files:** Create `docs/architecture.md` (from `v1-architecture.md`, product terms only) and `docs/deployments/generality.md`; delete `docs/v1-architecture.md`; update links (`roadmap.md`, specs, `findings-cli.md`, `review-files.md`, `README.md` if it links); move the table site spec's "Implementation notes" into the deployment doc, leaving a one-line pointer; document `audit.toml`, store `pilot.yaml`, remote log sources and `--manifest` in `findings-cli.md`.

**Gate:** pre-commit (mdformat), basedpyright, full suite; `grep -rn "v1-architecture" docs README.md` empty.

### Task 5: the findings repo becomes the deployment repository (Generality-Labs/inspect-evals-findings, separate PR)

Remove the store copies now served from the bucket (`*/runs`, `*/SUMMARY.md`, `SUMMARY.md`, `export/`, `*.parquet`); add `audit.toml` (`[evals] root = "../inspect_evals"`), `hawk-artefacts.yaml` (moved from inspect_audit), and a README describing the repository as configuration for a store at `s3://inspect-evals-findings`, how to load credentials and run a sweep, and that the git history keeps the pre-R2 copies. Upload `audit.toml` to the bucket too, since the CLI reads it from the store root.

______________________________________________________________________

## Self-review notes

- Spec coverage: `audit.toml` (`[evals]` only; `[tracker]` and `[publish]` deferred with reasons), store `pilot.yaml`, `s3://` logs (generalised to any fsspec scheme), Hawk manifest moved, doc split, findings repo trimmed. The nightly workflow is out by Matt's decision.
- Review Focus 1 to 5 map to Task 1 (1, 2), Task 2 (3, 4), Task 3 (5).
