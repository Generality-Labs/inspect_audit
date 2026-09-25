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

`findings/models.py`, `fingerprint.py`, `io.py` and `adapters/` import nothing from the rest of inspect_audit, so the module can be lifted into its own package later. `cli.py` is the one exception: it imports `inspect_audit._registry.fetch_logs` to resolve `hawk:` log sources. Nothing else in inspect_audit imports `findings`.

## Models

All pydantic v2, `extra="forbid"` on envelope models.

**Subject.** `eval: str` (registry name, `inspect_evals/stereoset`); `revision: Revision` with `commit: str | None`, `package_version: str | None`, `dirty: bool | None`, at least one of commit or package_version required; `task_version: TaskVersion | None` with `full: str`, `comparability: int | None`, `interface: str | None`; `dataset: DatasetRef | None` with `path`, `config`, `split`, `revision`, all `str | None`; `task_args: dict[str, JsonValue]` default empty.

**Dimension.** `Literal["construct", "contentvalidity", "dataset", "scaffold", "harness", "environment", "grading", "resources", "informativeness"]`, the keys the GL framework uses in `investigation/report/framework/Content.tex` and that `_assessment.framework_definitions` parses from `auditframework.sty`. The Literal is static because pydantic needs it so; `test_models.py` asserts it equals the parsed keys, so the two cannot drift. Moving the parser to a neutral module shared by the report and this module is deferred.

**Severity.** `Literal["none", "minor", "major", "critical"]`.

**Status.** `Literal["hypothesis", "supported", "qualified", "retracted"]`.

**Location.** A discriminated union on `kind`, each subclass `extra="allow"` and each implementing `key() -> str`:

| kind | required fields | key |
| --- | --- | --- |
| `code` | `file`; optional `line`, `end_line`, `column` | `code:{file}:{line or 0}` |
| `sample` | `dataset`, `sample_id`; optional `field` | `sample:{dataset}:{sample_id}` |
| `transcript` | `eval_id`, `sample_uuid`; optional `message_id`, `event_uuid`, `sample_id`, `epoch` | `transcript:{eval_id}:{sample_uuid}:{message_id or event_uuid or ""}` |
| `log` | `eval_id`, `path`; optional `location_hint` | `log:{eval_id}:{path}` |
| `scorer` | `eval_id`, `scorer` | `scorer:{eval_id}:{scorer}` |
| `artifact` | `path`; optional `location` | `artifact:{path}:{location or ""}` |
| `url` | `url` | `url:{url}` |

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

| producer | default prefix | override |
| --- | --- | --- |
| lint | `["uvx", "--from", "inspect-evals-lint==0.7.0", "inspect-evals-lint"]` | `INSPECT_AUDIT_LINT_CMD` |
| dataset | `["uvx", "--from", "git+https://github.com/Generality-Labs/inspect_dataset@afbc94c0b509", "inspect-dataset"]` (origin/main on 2026-09-25; the package is not on PyPI) | `INSPECT_AUDIT_DATASET_CMD` |

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

Reads `eval.yaml`'s `external_assets[]` for the first entry with `type: huggingface`. If none, returns a skip run with reason `no huggingface asset`. Field names, config and split come from `DATASET_OVERRIDES: dict[str, dict[str, str]]` keyed by target, seeded with the StereoSet entry (`config: intersentence`, `split: validation`, `question_field: context`, `answer_field: sentences`, `id_field: id`). Without an entry the scan runs with auto-detection and a failure is a skip.

Command: `<prefix> scan <source> [--config C] [--split S] [--question-field Q --answer-field A --id-field I] -o <tmpdir>`. Static scanners only. Parse:

- `scan_summary.json` fills `subject.dataset` (`path`, `config`, `split`, `revision`) and one `Outcome(rule=scanner, status="pass" if total == 0 else "fail")` per scanner in `by_scanner`.
- Every row in every `<scanner>.json` becomes a `Finding`: primary `sample` location from `sample_id` (falling back to `sample_index`), `dimension` `dataset`, `severity` by `{"low": "none", "medium": "minor", "high": "major"}`, `summary` from `explanation` truncated to 200 characters, `source.record` the row verbatim, `source.format` `inspect_dataset.Finding@<version>` where the version comes from the summary if present else `unknown`.
- `dataset.revision` stays `None` in the prototype. The gap is recorded under Open gaps.
- The `answer_length` flood is expected and not filtered here. Suppression is an aggregator concern; the prototype makes the noise visible and the per-eval summary counts it separately so it does not hide the rest.

### Header

Pure Python over `.eval` files in `ctx.logs`, reading headers only. Always runs. For each log, `read_eval_log(header_only=True)`; keep those whose `eval.task_registry_name` or `eval.task` matches the target on the unqualified name. If none match, the run has one `skip` outcome `no logs for target`.

Checks, each producing an `Outcome` and, when it fires, one `Finding` with `source.eval_spec` set to the header's `eval` dump minus `dataset.sample_ids`:

| rule | condition | primary location | dimension, severity |
| --- | --- | --- | --- |
| `header.dataset_samples` | `eval.dataset.samples` differs from `eval.yaml`'s `tasks[].dataset_samples` for the matching task, and `eval.config.limit` is null or `eval.dataset.samples` is the pre-limit size | `log` at `eval.dataset.samples` | `dataset`, `minor` |
| `header.version_drift` | across the matched logs, more than one distinct `task_version` or `packages.inspect_evals` | `log` at `eval.task_version` on the newest log | `informativeness`, `minor`; one finding per log set, related locations on every other log |
| `header.unscored_samples` | any `results.scores[].unscored_samples > 0` | `scorer` for that scorer | `grading`, `minor` |
| `header.dirty_revision` | `eval.revision.dirty` is true | `log` at `eval.revision.dirty` | `informativeness`, `none` |
| `header.unknown_sample_ids` | `--resolve` only: any sample id in `eval.dataset.sample_ids` absent from the resolved task's dataset | `log` at `eval.dataset.sample_ids` | `dataset`, `major` |

`header.unknown_sample_ids` resolves the task with `inspect_audit._resolve.resolve_task` and is the one place the header adapter imports from inspect_audit. It is behind `--resolve` because it needs inspect_evals importable and may download a dataset. Its severity is `major` because it means recorded attempts cannot be joined to the current dataset.

`eval.yaml` lookup is shared with the other adapters: `ie_root / "src/inspect_evals" / <package> / "eval.yaml"`.

## CLI

Console script `inspect-audit-findings`, registered in `pyproject.toml` under `[project.scripts]`.

```text
inspect-audit-findings run --root <ie_root> --logs <source>... --out <dir>
    [--producers lint,dataset] [--resolve] [--featured] [TARGET ...]
inspect-audit-findings summary <out dir>
```

- `TARGET` is a registry name. `--featured` appends the 35 ids from `featured.py` as `inspect_evals/<id>`. At least one target is required.
- `--logs` accepts, repeatedly, a directory, a `.eval` file, or a `hawk:<eval-set-id>` address. Local sources are listed with `list_eval_logs(recursive=True)`. `hawk:` sources are materialised once per invocation with `inspect_audit._registry.fetch_logs`, which downloads through the Hawk API using `HAWK_API_URL` and either `HAWK_ACCESS_TOKEN` or the runner refresh environment. Logs are then partitioned by target on the header's task name, so one `--logs` directory can serve a whole sweep.
- `--producers` selects external producers, default `lint,dataset`. The header producer runs whenever `--logs` was supplied; without logs it is not requested, so a lint-and-dataset sweep can exit 0.
- For each target, in order: header, then the selected producers. Each writes `<out>/<slug>/<producer>.run.json` and appends to in-memory lists. After the sweep, `findings.parquet`, `runs.parquet`, `<out>/SUMMARY.md` and each `<out>/<slug>/SUMMARY.md` are written.
- Exit code 0 if every producer ran; 1 if any run was a skip; 2 on a usage error. Findings do not affect the exit code.
- `summary` re-renders every `SUMMARY.md` from the `*.run.json` files without re-running producers.

## Outputs

```text
<out>/
  <slug>/header.run.json  lint.run.json  dataset.run.json
  <slug>/SUMMARY.md
  findings.parquet
  runs.parquet
  SUMMARY.md
```

`findings.parquet` has the envelope flattened to columns (`fingerprint`, `subject_eval`, `subject_revision_commit`, `subject_task_version_full`, `dimension`, `severity`, `status`, `summary`, `primary_kind`, `primary_key`, `run_id`, `producer`, `rule`) plus `source` and `locations` as JSON strings. `runs.parquet` has one row per run with outcome counts, duration and whether it was skipped.

`render.py` produces both summaries from `Run` models with plain string templating and no model calls, which is why `summary` can regenerate them from the `*.run.json` files. Per-eval `SUMMARY.md`: subject block (eval, revision, task version, dataset); one table of outcomes across producers (rule, status, message); counts of findings by producer and by dimension and severity; then every finding as a line with severity, dimension, rule, primary location key and summary, grouped by producer, with a note on any rule that produced more than 100 findings so noise is visible at a glance. Sweep `SUMMARY.md`: one row per eval with per-producer status and finding counts.

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

Then read the six summaries and write `agent_artefacts/findings_prototype/ACCEPTANCE.md` listing, per eval, what the output got wrong or missed, and what the aggregator will need first. That list is the input to the next design conversation.

## Open gaps recorded, not solved

- Dataset revision is not in Inspect logs and not in inspect-dataset's output unless passed. inspect_evals could write its pinned revision into task metadata; Inspect could add it to `EvalDataset`.
- The Featured list is a hard-coded set in a docs template. inspect_evals should expose it in `eval.yaml` or a listing file.
- inspect-dataset field auto-detection (inspect_dataset#27) and struct-typed answers (inspect_dataset#26).
- inspect_audit raising on unknown sample ids (inspect_audit#2) and leaving probe containers running (inspect_audit#3).
- Lint's rule-to-dimension table belongs in lint's rule registry eventually, not in this adapter.

## Commits

Atomic, in this order: `.gitignore` for worktrees; schema docs and this spec; models and fingerprint with tests and schema files; IO with tests; render with tests; producers config and shared adapter helpers; lint adapter with fixture and tests; dataset adapter with fixtures and tests; header adapter with tests; featured list and CLI with tests; pyproject script entry; acceptance run artefacts. No push to `main`; a PR against `dev/integrated-audits` when it is worth sharing.
