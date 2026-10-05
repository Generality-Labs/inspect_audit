# Findings prototype: design

Date: 2026-09-25. Branch: `findings-prototype` off `dev/integrated-audits` at 8c2748b.

## Purpose

The Q4 plan makes inspect_audit the source of a findings database for Inspect Evals: one queryable record set across roughly 130 evals, rendered on a hosted site, feeding a maintenance agent. Two schema proposals exist in `docs/finding-schema.md` (full normalisation) and `docs/finding-schema-envelope.md` (thin envelope plus verbatim producer record). The second was chosen. This prototype is the first step of the integration plan: make the schema real, write the deterministic producers' adapters, and run them end to end over a handful of evals so the next decisions are made from output rather than from documents.

What the user said: fork off the dev branch and put together a prototype to play with; integrate inspect-evals-lint and inspect-dataset at the file level; the header producer always runs; logs may come from Hawk, where a large store is accumulating. Assumptions made here: models should be close to final but small; the pipeline is a script, not a product; no agent involvement and no hosting in this step.

Success is running the CLI over StereoSet and five Featured evals with local logs, reading the per-eval summaries, and producing a written list of what the output got wrong.

## Scope

In: a `findings` module in inspect_audit with pydantic models, a fingerprint function, JSON and parquet IO, three adapters (lint, inspect-dataset, log headers), a console script, fixtures and tests, and the two schema documents committed alongside this spec.

Out: aggregation across runs, `baseline_state`, alias merging, a suppression store, HTML rendering, the investigator agent using the module, Scout adapter, publishing the module as its own package. Each is a later step that depends on seeing this output first.

## Package layout

```text
src/inspect_audit/findings/
  __init__.py       public re-exports
  models.py         Run, Finding, Subject, Revision, TaskVersion, DatasetRef, Source,
                    Location union, Outcome, Effect, Suppression, StatusChange, Provenance
  fingerprint.py    fingerprint(producer, rule, eval, primary), FINGERPRINT_VERSION
  io.py             write_run, read_runs, findings_df, runs_df, write_parquet
  producers.py      ProducerConfig: command prefixes, pinned specs, env overrides
  adapters/
    __init__.py
    lint.py         inspect-evals-lint JSON -> Run
    dataset.py      inspect-dataset scan output -> Run
    header.py       .eval headers -> Run
  featured.py       the 35 Featured eval ids, copied from inspect_evals docs/_templates/evals.ejs
  hawk.py           find_eval_sets(task) via the hawk client; download_eval_set / download_artifacts / pull_manifest via the hawk CLI
  render.py         render_eval_summary(runs) and render_sweep_summary(runs) -> markdown
  cli.py            inspect-audit-findings
  schema/
    finding.schema.json
    run.schema.json
tests/findings/
  fixtures/         lint.json, scan_summary.json, duplicate_questions.json from the 2026-09-25 StereoSet pass
  test_models.py test_fingerprint.py test_io.py test_render.py
  test_lint_adapter.py test_dataset_adapter.py test_header_adapter.py test_cli.py
```

`findings/` imports nothing from the rest of inspect_audit except `adapters/header.py`, which imports `inspect_audit._resolve.resolve_task` behind `--resolve`. Header findings take their subject from the log header itself (`eval.revision`, `eval.packages`, `eval.task_version`, `eval.task_args`) rather than from the checkout, and the header run's `inputs.comparison` names the checkout whose `eval.yaml` was compared against (changed 2026-09-29). `hawk:` log sources are handled by `findings/hawk.py` (added 2026-09-28): discovery through the optional `hawk` Python client, which resolves the operator's `hawk login` token from the keyring, and download by shelling out to the `hawk` CLI into a cache under `~/.cache/inspect_audit/hawk/`. The earlier plan to reuse `_registry.fetch_logs` was dropped because it needs runner-style token environment variables and re-downloads into a temp directory every run. Nothing else in inspect_audit imports `findings`.

## Models

All pydantic v2, `extra="forbid"` on envelope models.

**Subject.** `eval: str` (registry name, `inspect_evals/stereoset`); `revision: Revision` with `commit: str | None`, `package_version: str | None`, `dirty: bool | None`, at least one of commit or package_version required; `task_version: TaskVersion | None` with `full: str`, `comparability: int | None`, `interface: str | None`; `dataset: DatasetRef | None` with `path`, `config`, `split`, `revision`, all `str | None`; `task_args: dict[str, JsonValue]` default empty.

**Taxonomy, Dimension, Check.** Taxonomies are versioned data under `findings/taxonomies/` (`gl-audit@1` generated from `auditframework.sty`, `gl-audit@2` transcribed from the Audit Reports Strategy document as a draft, plus a v1-to-v2 mapping). `Finding.taxonomy` defaults to `gl-audit@1`; `dimension` and optional `check` are open strings validated against that taxonomy by a model validator. `test_taxonomy.py` asserts v1 equals what `_assessment.framework_definitions` parses from the `.sty`, so the two cannot drift; generating the `.sty` from the data file is a later change on the LaTeX side. Added 2026-09-28 after the strategy document made the nine-dimension list a moving target.

**Severity.** `Literal["none", "minor", "major", "critical"]`.

**Status.** `Literal["hypothesis", "supported", "qualified", "retracted"]`.

**Location.** A discriminated union on `kind`, each subclass `extra="allow"` and each implementing `key() -> str`:

| kind         | required fields                                                                     | key                                                                   |
| ------------ | ----------------------------------------------------------------------------------- | --------------------------------------------------------------------- |
| `code`       | `file`; optional `line`, `end_line`, `column`                                       | `code:{file}:{line or 0}`                                             |
| `sample`     | `dataset`, `sample_id`; optional `field`                                            | `sample:{dataset}:{sample_id}`                                        |
| `transcript` | `eval_id`, `sample_uuid`; optional `message_id`, `event_uuid`, `sample_id`, `epoch` | `transcript:{eval_id}:{sample_uuid}:{message_id or event_uuid or ""}` |
| `log`        | `eval_id`, `path`; optional `location_hint`                                         | `log:{eval_id}:{path}`                                                |
| `scorer`     | `eval_id`, `scorer`                                                                 | `scorer:{eval_id}:{scorer}`                                           |
| `artifact`   | `path`; optional `location`                                                         | `artifact:{path}:{location or ""}`                                    |
| `url`        | `url`                                                                               | `url:{url}`                                                           |

Every location has `role: Literal["primary", "related"]` default `related` and `quote: str | None`. `Finding` validates that exactly one location has `role == "primary"`.

**Provenance.** `timestamp: AwareDatetime`, `author: str`, `reason: str | None`, `metadata: dict[str, JsonValue]`, mirroring `inspect_ai.log.ProvenanceData`. Used by `Suppression` (`kind: str`, `provenance`) and `StatusChange` (`status`, `provenance`).

**Effect.** `affected: int | None`, `denominator: int | None`, `score: Literal["measured", "hypothesised"] | None`, `description: str | None`.

**Source.** `format: str` (type and package version, `inspect_evals_lint.Diagnostic@0.7.0`), `record: JsonValue`, `eval_spec: dict[str, JsonValue] | None`. Not re-validated against producer models.

**Finding.** `schema_version: Literal["0.1"]`, `fingerprint: str`, `fingerprint_version: int`, `producer: str`, `rule: str`, `subject`, `dimension`, `severity`, `status`, `summary: str`, `locations: list[AnyLocation]` (min length 1, exactly one primary), `run_id: str`, `source: Source`, `aliases: list[str]`, `suppressions: list[Suppression]`, `history: list[StatusChange]`, `introduced: VersionRef | None`, `fixed: VersionRef | None`, `effect: Effect | None`. `VersionRef` is `commit: str | None`, `comparability_version: int | None`. A computed property `primary_location` returns the primary.

**Outcome.** `rule: str`, `status: Literal["pass", "fail", "skip"]`, `message: str | None`.

**Run.** `id: str`, `timestamp: AwareDatetime`, `producer: str`, `producer_version: str | None`, `git_commit: str | None`, `subject: Subject`, `inputs: dict[str, JsonValue]`, `model: str | None`, `cost_usd: float | None`, `duration_s: float | None`, `outcomes: list[Outcome]`, `findings: list[Finding]`. One `Run` per producer per eval per invocation.

## Fingerprint

```python
FINGERPRINT_VERSION = 1

def fingerprint(producer: str, rule: str, eval: str, primary: Location) -> str:
    return "sha256:" + sha256(f"{producer}|{rule}|{eval}|{primary.key()}".encode()).hexdigest()
```

Adapters call it once at write time and store the result with `fingerprint_version`. It is never recomputed on read. A recipe change bumps the version and is a migration. The claim-without-a-line case for agent-produced findings is out of scope.

## Schema files

`tests/findings/test_models.py` regenerates `finding.schema.json` and `run.schema.json` with `model_json_schema()` and fails if the committed files differ, matching how inspect_audit treats `findings.schema.json`.

## Producers and adapters

### Common

`ProducerConfig` holds, per external producer, a command prefix as a list of strings and a pinned spec. Defaults:

| producer | default prefix                                                          | override                    |
| -------- | ----------------------------------------------------------------------- | --------------------------- |
| lint     | `["uvx", "--from", "inspect-evals-lint==0.10.0", "inspect-evals-lint"]` | `INSPECT_AUDIT_LINT_CMD`    |
| dataset  | `["uvx", "--from", "inspect-dataset==0.5.0", "inspect-dataset"]`        | `INSPECT_AUDIT_DATASET_CMD` |

An override is a shell-split string. Each adapter exposes two functions: `parse(...) -> Run` which is pure and tested against fixtures, and `run(target, ctx) -> Run` which invokes the subprocess with a timeout, then calls `parse`. Any failure in `run` (non-zero exit, timeout, missing binary, unparseable output) returns a `Run` with a single `Outcome(rule="<producer>", status="skip", message=<error tail>)` and no findings. Nothing raises past the adapter.

`ctx` is a `Context` dataclass: `ie_root: Path`, `logs: list[Path]` (already-local log files for the target), `out_dir: Path`, `producers: ProducerConfig`, `resolve: bool`.

Common envelope derivation, shared in `adapters/__init__.py`:

- `subject.eval` is the target as given.
- `subject.revision.commit` from `git -C ie_root rev-parse HEAD`, `dirty` from `git status --porcelain` being non-empty, `package_version` from `importlib.metadata.version("inspect_evals")` when importable else `None`.
- `subject.task_version` from `eval.yaml`'s `version` (`"3-A"` splits into full, comparability, interface); `None` if missing.
- `run.id` is `f"{producer}-{utc timestamp}-{slug(target)}"`.

### Lint

Command: `<prefix> --root <ie_root> <package> --output-format json`, where `<package>` is the last path segment of the target. Parse:

- Every `packages[].outcomes[]` row becomes `Outcome(rule=code, status, message)`.
- Every `packages[].diagnostics[]` row becomes a `Finding`: primary `code` location from `file`, `line`, `column`; `summary` from `message`; `source.format` `inspect_evals_lint.Diagnostic@<version from the JSON>`; `source.record` the row verbatim; `status` `supported`.
- `dimension` and `severity` from `LINT_RULES: dict[str, tuple[Dimension, Severity]]`, default `("harness", "minor")`, with these overrides: `IEBP005`, `IEBP006`, `IEBP007` to `environment`; `IEBP008`, `IEBP009` to `dataset`; `IEBP001`, `IEBP002` to `grading`. The table is the expected point of argument and is one dict.
- `subject.eval` is `inspect_evals/<package>`; lint findings are package-scoped.

### Dataset

By default the dataset is scanned through the eval's task: the path passed to `inspect-dataset scan` is `inspect_evals/<task>`, the declared `task` or the first one in `eval.yaml`, so the scan loads samples with the split, config, revision, field mapping and sample ids the eval itself uses (PR #16, 2026-09-30). That scan imports the eval, so it runs in the checkout's own environment (`uv run --project <root> --frozen --with <inspect-dataset> inspect-dataset`, overridable with `INSPECT_AUDIT_DATASET_TASK_CMD`). An entry in `pilot.yaml` (`findings/config.py`: `DatasetConfig`) that declares HuggingFace settings (path, config, split, revision, field roles) scans the HuggingFace dataset directly instead, with `eval.yaml`'s asset as the default path. `Run.inputs.dataset` records `mode` (`task` or `hf`), what was scanned, and whether a declaration was consulted. With no task, no asset and no declaration the producer skips, naming what it looked for. An eval's entry inherits every field it does not set from the file's `defaults`.

Command: `<prefix> scan <source> [--config C] [--split S] [--question-field Q --answer-field A --id-field I] -o <tmpdir>`. Static scanners only. Parse:

- `scan_summary.json` fills `subject.dataset` (`path`, `config`, `split`, `revision`) and one `Outcome` per scanner: `fail` if it has findings in `by_scanner`, `skip` with the scanner's reason if `scanner_status` says `not_applicable`, otherwise `pass`. `scanner_status` arrived in inspect-dataset 0.5.0, which also stopped listing clean scanners in `by_scanner`; a summary without it is read from `by_scanner` alone (changed 2026-10-05).
- Every row in every `<scanner>.json` becomes a `Finding`: primary `sample` location from `sample_id` (falling back to `sample_index`), `dimension` `dataset`, `severity` by `{"low": "none", "medium": "minor", "high": "major"}`, `summary` from `explanation` truncated to 200 characters, `source.record` the row verbatim, `source.format` `inspect_dataset.Finding@<version>` where the version comes from the summary if present else `unknown`.
- `dataset.revision` stays `None` in the prototype. The gap is recorded under Open gaps.
- The `answer_length` flood is expected and not filtered here. Suppression is an aggregator concern; the prototype makes the noise visible and the per-eval summary counts it separately so it does not hide the rest.

### Header

Pure Python over `.eval` files in `ctx.logs`, reading headers only. Always runs. For each log, `read_eval_log(header_only=True)`; keep those whose `eval.task_registry_name` or `eval.task` matches the target on the unqualified name. If none match, the run has one `skip` outcome `no logs for target`.

A qualified registry name must share the target's package: `audit/inspect_evals/scicode` is not a scicode log. Matched logs then pass the eval's `LogFilter`: mock-model runs are excluded unless `include_mock`, and a declared `task_args` excludes other configurations. Default versus variant is judged on `task_args_passed`, the arguments the operator gave, because resolved `task_args` carry defaults for every parameterised task. Logs with non-default passed arguments still join the drift, unscored and dirty checks but are not compared against the declared sample count. `Run.inputs.logs` records `used`, `excluded` and `count_excluded` with reasons; when every matched log is excluded the run is a skip that says why (changed 2026-09-30).

Checks, each producing an `Outcome` and, when it fires, one `Finding` with `source.eval_spec` set to the header's `eval` dump minus `dataset.sample_ids`:

| rule                        | condition                                                                                                                                                                          | primary location                               | dimension, severity                                                                       |
| --------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------- | ----------------------------------------------------------------------------------------- |
| `header.dataset_samples`    | `eval.dataset.samples` differs from `eval.yaml`'s `tasks[].dataset_samples` for the matching task, and `eval.config.limit` is null or `eval.dataset.samples` is the pre-limit size | `log` at `eval.dataset.samples`                | `dataset`, `minor`                                                                        |
| `header.version_drift`      | across the matched logs, more than one distinct `task_version` or `packages.inspect_evals`                                                                                         | `log` at `eval.task_version` on the newest log | `informativeness`, `minor`; one finding per log set, related locations on every other log |
| `header.unscored_samples`   | any `results.scores[].unscored_samples > 0`                                                                                                                                        | `scorer` for that scorer                       | `grading`, `minor`                                                                        |
| `header.dirty_revision`     | `eval.revision.dirty` is true                                                                                                                                                      | `log` at `eval.revision.dirty`                 | `informativeness`, `none`                                                                 |
| `header.unknown_sample_ids` | `--resolve` only: any sample id in `eval.dataset.sample_ids` absent from the resolved task's dataset                                                                               | `log` at `eval.dataset.sample_ids`             | `dataset`, `major`                                                                        |

`header.unknown_sample_ids` resolves the task with `inspect_audit._resolve.resolve_task` and is the one place the header adapter imports from inspect_audit. It is behind `--resolve` because it needs inspect_evals importable and may download a dataset. Its severity is `major` because it means recorded attempts cannot be joined to the current dataset.

`eval.yaml` lookup is shared with the other adapters: `ie_root / "src/inspect_evals" / <package> / "eval.yaml"`.

## CLI

Console script `inspect-audit-findings`, registered in `pyproject.toml` under `[project.scripts]`.

```text
inspect-audit-findings run --root <ie_root> --logs <source>... --out <dir>
    [--producers lint,dataset] [--resolve] [--config pilot.yaml] [--featured] [TARGET ...]
inspect-audit-findings summary <out dir>
```

- `TARGET` is a registry name. `--featured` appends the 35 ids from `featured.py` as `inspect_evals/<id>`. At least one target is required.
- `--logs` accepts, repeatedly, a directory, a `.eval` file, or a `hawk:<eval-set-id>` address. Local sources are listed with `list_eval_logs(recursive=True)`. `hawk:` sources are pulled with `hawk download` into `--hawk-cache` (default `~/.cache/inspect_audit/hawk/<set>`), where the CLI skips files already present. `--hawk-task <task>` resolves to every eval set whose `task_names` include the task, refusing above `--hawk-limit` (default 20) so a task with hundreds of sets is not pulled by accident; `hawk-sets <task>` lists them without pulling. Logs are then partitioned by target on the header's task name, so one corpus can serve a whole sweep.
- `hawk-pull [--manifest scripts/hawk-artefacts.yaml] [--dest DIR]` fetches a declared working set instead of a per-run cache: the manifest names eval sets under `logs:` and investigator bundles under `artifacts:` (each `id` plus a free-text `note`), and they land in `<dest>/logs/<set>/` and `<dest>/artifacts/<set>/`. The default dest `artefacts/hawk` is gitignored, so anyone with `hawk login` can reproduce the same local inputs without anything private entering the repo. A failed entry is reported and the rest still pull; exit 1 if any failed. `hawk download-artifacts` needs `aiofiles`, which the 3.5.0 `hawk[cli]` extra omits, so the `remote` and `dev` extras add it.
- `--producers` selects external producers, default `lint,dataset`. The header producer runs whenever `--logs` was supplied; without logs it is not requested, so a lint-and-dataset sweep can exit 0.
- `--config PATH` (default: the packaged `findings/pilot.yaml`) declares per eval what to scan and which logs count. A file that fails validation is a usage error naming the file.
- `--review DIR` (default: `--out`) names the directory holding `suppressions.yaml` and `issues.yaml`; both are applied to copies of the current runs before summaries and parquet are written, and a malformed file is a usage error naming the file. Every `Finding` carries an `id` of the form `<run id>/<n>`, assigned by its run when the producer set none.
- For each target, in order: header, then the selected producers. Each run is written to `<out>/<slug>/runs/<run id>.run.json` and never overwritten (a name collision within one second gets a `-2` suffix). The sweep then rewrites `<out>/<slug>/current.json`, a manifest from producer name to the run file that producer's current view uses; producers that did not run keep their previous entry. `findings.parquet`, `runs.parquet`, `<out>/SUMMARY.md` and each `<out>/<slug>/SUMMARY.md` are rendered from the runs the manifests select, so a partial sweep never mixes fresh and stale results by accident and history is never lost (changed 2026-09-29 after the prototype review).
- Exit code 0 if every producer ran; 1 if any run was a skip; 2 on a usage error. Findings do not affect the exit code.
- `summary` re-renders every `SUMMARY.md` and both parquet files from the runs `current.json` selects, without re-running producers. `read_runs` still walks every run file for history.

## Outputs

```text
<out>/
  <slug>/runs/<run id>.run.json   one per producer invocation, immutable
  <slug>/current.json             producer -> runs/<run id>.run.json
  <slug>/SUMMARY.md
  findings.parquet
  runs.parquet
  SUMMARY.md
  suppressions.yaml               optional, written by a person; see docs/review-files.md
  issues.yaml                     optional, written by a person
```

The two review files may sit beside the output or in `--review DIR`. They are applied to copies of the current runs before summaries and parquet are written; run files are never edited. Summaries group findings by rule with a count and two examples, list suppressed observations with their reasons, and list accepted issues with the number of current observations linked to each.

`findings.parquet` has the envelope flattened to columns: identity (`id`, the record id `<run id>/<n>`; `fingerprint`, `fingerprint_version`, `schema_version`), the whole subject (`subject_eval`, revision commit, package version and dirty flag, task version full, comparability and interface, dataset path, config, split and revision, `subject_task_args` as JSON), taxonomy position, severity, status, summary, primary location kind and key, run and producer, and the review fields (`aliases`, `suppressions`, `suppressed`, `issue`, `history`, `introduced`, `fixed`, `effect`) as JSON strings, booleans or null, plus `source` and `locations` as JSON strings. A consumer reading only the parquet has everything the envelope holds. `runs.parquet` has one row per run with outcome counts, duration and whether it was skipped.

`render.py` produces both summaries from `Run` models with plain string templating and no model calls, which is why `summary` can regenerate them from the selected run files. Per-eval `SUMMARY.md`: subject block (eval, revision, task version, dataset); one table of outcomes across producers (rule, status, message); counts of findings by producer and by dimension and severity; then the active findings per producer grouped by severity, dimension and rule, a single line for a group of one and a count plus two example lines otherwise, with a note on any rule that produced more than 100 active findings so noise is visible at a glance; then a Suppressed section (rule, count, kind, author, reason) and an Issues section (id, title, current observation count, link) when there is anything to show. Sweep `SUMMARY.md`: one row per eval with per-producer status and finding counts. An Inputs section lists the dataset scanned and its origin, logs used and excluded with reasons, and the comparison revision; the sweep table shows log counts per header run.

## Testing

- `test_models.py`: JSON round trip for `Run`; union discrimination for every `Location` kind; rejection of zero or two primaries; schema regeneration and diff.
- `test_fingerprint.py`: golden value for a known input; changes with each of producer, rule, eval and primary key; unchanged when related locations change.
- `test_io.py`: write and read a run; `findings_df` column set; parquet round trip.
- `test_render.py`: both summaries against fixture runs, including the over-100-findings note and a skipped producer.
- `test_lint_adapter.py`: `parse` on `fixtures/lint.json` yields 25 outcomes and one finding with the expected code location, dimension `dataset`, severity `minor`; `run` with the prefix pointed at a stub script that cats the fixture; `run` with a prefix that exits 2 yields a skip run, while exit 1 (lint's "checks failed") still parses.
- `test_dataset_adapter.py`: `parse` on the fixture directory yields three outcomes and 18 plus 2,123 plus 28 findings with `sample` primaries and the severity mapping; `run` with a stub; a target with no HuggingFace asset yields a skip.
- `test_header_adapter.py`: real logs from `test_helpers.logs.run_fixture_eval` in a temporary root whose `eval.yaml` says `dataset_samples: 99`, asserting `header.dataset_samples` fires with a `log` primary and `eval_spec` present; a second log at a different task version asserting `header.version_drift`; no logs for the target yields a skip.
- `test_cli.py`: `run` over the temporary root with both external prefixes stubbed, asserting the file layout, the parquet row counts, exit code 0; with one stub failing, exit code 1 and a skip recorded; `summary` regenerates identical markdown.
- One test gated on `INSPECT_AUDIT_LIVE_TESTS=1` runs the real lint through `uvx` against a real inspect_evals checkout given by `INSPECT_EVALS_ROOT`.

Nothing is Docker-marked. `make check` and `make test` stay green. Stub scripts live under `tests/findings/stubs/` and are plain Python files invoked as `[sys.executable, stub]`.

## Acceptance

From the inspect_evals checkout's environment:

```text
uv run --with <worktree> inspect-audit-findings run --root . --logs logs --out /tmp/findings \
    inspect_evals/stereoset inspect_evals/hle inspect_evals/agentharm inspect_evals/xstest \
    inspect_evals/strong_reject inspect_evals/simpleqa
```

Then read the six summaries and write `agent_artefacts/findings_prototype/ACCEPTANCE.md` (the `agent_artefacts/` directory is gitignored: audit outputs and internal planning stay in the private `~/Developer/inspect_ai/audit-artefacts` copy, not in this public repository) listing, per eval, what the output got wrong or missed, and what the aggregator will need first. That list is the input to the next design conversation.

## Open gaps recorded, not solved

- Dataset revision is not in Inspect logs and not in inspect-dataset's output unless passed. inspect_evals could write its pinned revision into task metadata; Inspect could add it to `EvalDataset`.
- The Featured list is a hard-coded set in a docs template. inspect_evals should expose it in `eval.yaml` or a listing file.
- inspect-dataset field auto-detection (inspect_dataset#27) and struct-typed answers (inspect_dataset#26).
- inspect_audit raising on unknown sample ids (inspect_audit#2) and leaving probe containers running (inspect_audit#3).
- Lint's rule-to-dimension table belongs in lint's rule registry eventually, not in this adapter.

## Commits

Atomic, in this order: `.gitignore` for worktrees; schema docs and this spec; models and fingerprint with tests and schema files; IO with tests; render with tests; producers config and shared adapter helpers; lint adapter with fixture and tests; dataset adapter with fixtures and tests; header adapter with tests; featured list and CLI with tests; pyproject script entry; acceptance run artefacts. No push to `main`; a PR against `dev/integrated-audits` when it is worth sharing.
