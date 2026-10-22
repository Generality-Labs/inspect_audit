# Testing and compatibility

## Three distinct checks

1. **Ordinary tests:** `uv run pytest`. These exercise contracts and Inspect behaviour without external model calls. Docker tests are deselected by default.
2. **Real containers:** `uv pip install --no-deps -e tests/compatibility`, then `uv run --no-sync pytest -m docker`. The PR gate runs the benchmark setup/grading/reset test and the child-job lifecycle test. Main and manual CI run the full Docker suite.
3. **External benchmark compatibility:** the API-free suite below runs real benchmark packages and environments on Hawk. It tests execution, not whether an agent's scientific conclusions are correct.

The ordinary suite still uses `mockllm` where scripted model outputs are useful. The external suite uses a registered `ModelAPI` driver that emits deterministic tool calls. Its judge model raises instead of fabricating answers. Hawk requires a model declaration and overrides `Task.model`, so the driver is bound directly to the agent. Public price metadata is fetched for the declaration; no completion is requested from it. An observed external model call fails the compatibility result.

## External suite

The case templates live in `tests/compatibility/cases`. They pin benchmark code and, where supported by the benchmark, dataset versions and container images. `@SELF` is replaced only for this repository; the runner tests the requested published IA commit.

```sh
uv sync --locked
uv run python tests/compatibility/run_suite.py --prepare-only
```

This validates all ten rendered Hawk configs without authentication, containers or model calls.

With `HAWK_API_URL` set and an existing `hawk login`:

```sh
uv run python tests/compatibility/run_suite.py --output out/compatibility
```

The commit must be pushed so Hawk can install it. Use `--ref FULL_SHA` to select another published commit. `--cases mirrorcode,harbor` selects cases. No additional concurrency limit is imposed; Hawk schedules the jobs.

Gated cases need the token names declared in their specification. Locally, HF_TOKEN can come from the Hugging Face token cache. Values pass through Hawk's secrets API, not the checked-in YAML. The case's non-secret runner environment is explicit. In investigations, declare the same settings under `benchmark.environment` and on the investigator runner; the frozen launch policy applies them to every child job. An undeclared host variable is not forwarded.

For a non-blocking launch:

```sh
uv run python tests/compatibility/run_suite.py --submit-only --output out/compatibility
uv run python tests/compatibility/run_suite.py --resume --output out/compatibility
```

The suite saves job IDs immediately and resumes monitoring existing jobs rather than resubmitting them. Its default deadline is 30 minutes from submission; timed-out and stuck jobs receive a stop request. Cancellation outside that path can leave jobs to inspect on Hawk.

## What a result means

Each probe exercises the production auditor's staging, source access, attempt tools, benchmark shell where present, grading, verdict recording and submission. The single validity verdict is deliberately UNVERIFIABLE: these probes do not establish benchmark correctness.

The separate compatibility score derives from actual tool results:

- **passed:** every applicable operation returned successfully, including a nonempty grade when a grader exists. An incorrect submission score is still successful grader execution.
- **partial:** a model-based grader was intentionally blocked. Its output has not been validated.
- **failed:** task/sandbox setup, a tool or the probe itself failed; an operation was not reached; or an external model call was observed.
- **blocked:** configuration, credentials or submission prevented the job from launching.
- **timed_out:** the deadline elapsed and stopping was requested.
- **not_applicable:** e.g. no log source was supplied or the task has no benchmark box. This does not prove log compatibility or environment behaviour.

Results are `out/compatibility/summary.json` and `summary.md`; native Inspect logs are downloaded under each case. The Hawk links retain runner/sandbox diagnostics when no eval log was written. Cleanup of every remote resource is not yet independently asserted by the summary.

The ten initial cases are SimpleQA, SciCode, GSM8K, BBH, AgentHarm, GPQA, HLE, MirrorCode, CORE-Bench and Terminal-Bench through Inspect Harbor. Each is a small selection, not exhaustive coverage. HLE's environment probe deliberately omits the known mismatching historical log so environment startup and log compatibility remain distinguishable. The original log mismatch still needs investigation. The Harbor case covers one sample-level Compose environment, not arbitrary heterogeneous multi-sample Harbor jobs.

## CI

`ci.yml` runs the ordinary checks and a real-container PR gate. `nightly.yml` checks compatibility with Inspect's main branch. `compatibility.yml` supports manual Hawk runs and a weekly opt-in schedule.

Hawk CI needs the HAWK_API_URL repository variable and a valid HAWK_ACCESS_TOKEN secret supplied by the CI identity's credential lifecycle; gated cases need HF_TOKEN. The repository currently has no such secrets configured. Scheduled execution is disabled unless HAWK_COMPATIBILITY_ENABLED is `true`. A one-off access token is insufficient for a permanent weekly schedule: configure automated credential renewal before enabling it. Personal Hawk login credentials are not copied into GitHub by this setup.

CI publishes the operational Markdown summary. Benchmark transcripts and gated dataset contents remain in Hawk and local downloaded artifacts, rather than being published as GitHub artifacts.

## Before release

Run the ordinary and Docker suites, then the external suite at the release candidate's exact commit. Review partial results and known failures individually. Passing functionality probes does not establish audit recall, false-positive rates, or report quality; those require separate labelled and paid agent evaluations.
