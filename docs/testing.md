# Testing and compatibility

## Three distinct checks

1. **Ordinary tests:** `uv run pytest`. These exercise contracts and Inspect behaviour without external model calls. Docker tests are deselected by default.
2. **Real containers:** `uv pip install --no-deps -e tests/compatibility`, then `uv run --no-sync pytest -m docker`. The PR gate runs the benchmark setup/grading/reset test and the child-job lifecycle test. Main and manual CI run the full Docker suite.
3. **External benchmark compatibility:** the API-free suite below runs real benchmark packages and environments on Hawk. It tests execution, not whether an agent's scientific conclusions are correct.

The ordinary suite still uses `mockllm` where scripted model outputs are useful. The external suite uses a registered `ModelAPI` driver that emits deterministic tool calls. Declared `mock_judges` use Inspect’s `mockllm` provider with explicit response fixtures. Other judge calls raise. Hawk requires a model declaration and overrides `Task.model`, so the driver is bound directly to the agent. Public price metadata is fetched for the declaration; no completion is requested from it. An observed external model call fails the compatibility result; the explicitly configured mock judge is recorded separately.

## External suite

The case templates live in `tests/compatibility/cases`. They pin benchmark code and, where supported by the benchmark, dataset versions and container images. `@SELF` is replaced only for this repository; the runner tests the requested published IA commit.

```sh
uv sync --locked
uv run python tests/compatibility/run_suite.py --prepare-only
```

This validates all rendered Hawk configs without authentication, containers or model calls.

With `HAWK_API_URL` set and an existing `hawk login`:

```sh
uv run python tests/compatibility/run_suite.py --output out/compatibility
```

The commit must be pushed so Hawk can install it. Use `--ref FULL_SHA` to select another published commit. `--cases mirrorcode,harbor` selects cases. No additional concurrency limit is imposed; Hawk schedules the jobs.

Gated cases need the token names declared in their specification. Locally, HF_TOKEN can come from the Hugging Face token cache. Values pass through Hawk's secrets API, not the checked-in YAML. The case's non-secret runner environment is explicit. In audits, declare the same settings under `benchmark.environment` and on the auditor runner; the frozen launch policy applies them to every child job. An undeclared host variable is not forwarded.

For a non-blocking launch:

```sh
uv run python tests/compatibility/run_suite.py --submit-only --output out/compatibility
uv run python tests/compatibility/run_suite.py --resume --output out/compatibility
```

The suite saves job IDs immediately and resumes monitoring existing jobs rather than resubmitting them. With `--resume --retry-blocked`, cases blocked before receiving a job ID may be submitted again; existing jobs are retained. Use a new output directory to rerun completed cases and preserve the previous evidence. Its default deadline is 30 minutes from submission; timed-out and stuck jobs receive a stop request. Cancellation outside that path can leave jobs to inspect on Hawk.

## What a result means

Each probe exercises the production investigator's staging, source access, attempt tools, benchmark shell where present, grading, verdict recording and submission. The single validity verdict is deliberately UNVERIFIABLE: these probes do not establish benchmark correctness.

The separate compatibility score derives from actual tool results:

- **passed:** every applicable operation returned successfully, including a nonempty grade when a grader exists. An incorrect submission score is still successful grader execution.
- **passed_mock** (grading stage): the real benchmark scorer parsed an explicit mock judge reply. This tests grading integration, not judge correctness.
- **partial:** a model-based grader was intentionally blocked. Its output has not been validated.
- **failed:** task/sandbox setup, a tool or the probe itself failed; an operation was not reached; or an external model call was observed.
- **blocked:** configuration, credentials or submission prevented the job from launching.
- **timed_out:** the deadline elapsed and stopping was requested.
- **not_applicable:** e.g. no log source was supplied or the task has no benchmark box. This does not prove log compatibility or environment behaviour.

Results are `out/compatibility/summary.json` and `summary.md`; native Inspect logs are downloaded under each case. The Hawk links retain runner/sandbox diagnostics when no eval log was written. Cleanup of every remote resource is not yet independently asserted by the summary.

The baseline cases are SimpleQA, SimpleQA Verified, SciCode, GSM8K, BBH, AgentHarm, GPQA, HLE, MirrorCode and Terminal-Bench through Inspect Harbor. Additional cases cover Cybench (`dynastic` and `delulu`) and AgentDojo (one banking injection task). Each is a small selection, not exhaustive coverage. HLE's environment probe deliberately omits the known mismatching historical log so environment startup and log compatibility remain distinguishable. The original log mismatch still needs audit. The Harbor case covers one sample-level Compose environment, not arbitrary heterogeneous multi-sample Harbor jobs.

## CI

`ci.yml` type-checks the production package and compatibility driver/runner, runs the ordinary checks, and runs a real-container PR gate. `nightly.yml` checks compatibility with Inspect's main branch. `compatibility.yml` supports manual Hawk runs and a weekly opt-in schedule.

Hawk CI needs the HAWK_API_URL repository variable and a valid HAWK_ACCESS_TOKEN secret supplied by the CI identity's credential lifecycle; gated cases need HF_TOKEN. The repository currently has no such secrets configured. Scheduled execution is disabled unless HAWK_COMPATIBILITY_ENABLED is `true`. A one-off access token is insufficient for a permanent weekly schedule: configure automated credential renewal before enabling it. Personal Hawk login credentials are not copied into GitHub by this setup.

CI publishes the operational Markdown summary. Benchmark transcripts and gated dataset contents remain in Hawk and local downloaded artifacts, rather than being published as GitHub artifacts.

## Before release

Run the ordinary and Docker suites, then the external suite at the release candidate's exact commit. Review partial results and known failures individually. Passing functionality probes does not establish investigation recall, false-positive rates, or report quality; those require separate labelled and paid agent evaluations.

## Initial validation and follow-up order (2026-10-22)

The local suite completed with **275 tests passed**, including the Docker tests. A separate regression also verifies that a recorded conversation longer than the investigator's message limit can be reconstructed without changing that limit. Both PR CI gates passed at `b36e32c`.

The first external batches identified and led to fixes for:

- Declared benchmark environment variables being lost in child jobs.
- Hawk overriding the task model and displacing the scripted compatibility driver.
- Recorded multiple-choice reasoning being treated as a bare answer letter.
- A sample sandbox overriding the merged task sandbox and removing the investigator service.
- Long grader JSON being truncated before the compatibility parser could read it.
- Historical transcript reconstruction incorrectly hitting the investigator's message limit.
- Concurrent Hawk CLI authentication refreshes racing over the same local keyring records.

GPQA, SciCode and MirrorCode passed after their fixes. GSM8K, BBH and the selected AgentHarm case passed in the initial functional batch. SimpleQA and HLE were partial because their model judges were intentionally blocked. Every inspected probe log recorded no external model calls. Native logs and earlier failures remain under their original output directories.

Harbor passed its actual sandbox and verifier test on Hawk after explicitly requesting `inspect-ai==0.3.264`, omitting the unused `remote` extra, and selecting the namespaced task `terminal-bench/make-mips-interpreter`. The earlier dependency failure is tracked in [#58](https://github.com/Generality-Labs/inspect_audit/issues/58).

CORE-Bench is excluded from the active suite. Its case is retained under `tests/compatibility/unsupported/` for reproduction: Hawk refuses its native Compose requirements (`privileged` and `extra_hosts`), and its build-only service needs a published image. Support is tracked in [#59](https://github.com/Generality-Labs/inspect_audit/issues/59).

The complex-case expansion uses the unchanged probe runtime at `40c703d5f1ff968413ad79cd9b02733d14ed72cb` and inspect-evals at `49738b6412cf3215db7c9d3d4838fc58b6f422a2`. Both Cybench cases and AgentDojo passed on Hawk with only the scripted driver recorded in model events. Results and native logs are in `out/compatibility-complex-20261022/`.

All three GDM in-house CTF cases are excluded from the active suite and retained under `tests/compatibility/unsupported/`. Their recorded failures follow. The `spray` case fails before execution: its attacker and target services use an `internal: true` network, while the added investigator service enables internet. Hawk grants internet per sandbox and refuses this combination because it cannot preserve the benchmark services' network isolation. Its isolation settings are not removed. `privesc_bash` independently fails because its Compose file declares `hostname: localhost`, while the converter accepts only the service name as hostname. It is retained as the `gdm-in-house-ctf-hostname` case. The separate `gdm-in-house-ctf-isolated` case uses native `privesc_7z`, which has `network_mode: none` and no custom hostname. Neither failure is worked around by rewriting benchmark settings. The hostname failure is recorded in `out/compatibility-complex-isolated-20261022/`; the 7z run is recorded separately in `out/compatibility-complex-isolated-7z-20261022/`. The first 7z attempt reached sandbox startup but failed when Docker Hub returned HTTP 500 while fetching an image layer; one identical retry is recorded in `out/compatibility-complex-isolated-7z-retry-20261022/`. That retry started the container and passed shell access, verdict recording and submission, but native grading failed: `SandboxEnvironment 'target_env' is not a recognized environment name`. The benchmark names `target_env` in its scorer and marks that service `x-default`; the Hawk/Kubernetes execution path does not expose it under the name the scorer requests. This remains a failing integration case, not a successful grading result.

The complete external baseline at runtime commit `24d92b25ed654285e32079175e9943680857b0ce` passed all ten remaining cases. SimpleQA, SimpleQA Verified and HLE used declared mock judges. These results establish the exercised execution paths, not investigation quality. The next order of work is:

1. Keep Harbor’s pinned runtime and native verifier execution covered by the suite.
2. Provision the renewable Hawk CI identity ([#57](https://github.com/Generality-Labs/inspect_audit/issues/57)), then enable the weekly suite.
3. Repeat the complete external suite when runtime code changes before treating it as a release gate.
4. Run paid auditor pilots and inspect their transcripts separately from this execution gate. Then measure recall against human labels.

Checkpoint resume remains separate ([#55](https://github.com/Generality-Labs/inspect_audit/issues/55)). The standalone deterministic findings package and its CLI have been removed. The audit register remains in the core package. Findings/report redesign is the next phase after execution tests.

## Native Kubernetes fixture

`tests/compatibility/cases/native-k8s.eval-set.yaml` runs the small `ia_compatibility/native_k8s` benchmark on Hawk. Its native Helm values provision a default service and a named helper. The grader checks supplied files, a sample setup script, an environment variable and access to the helper. It uses no model judge. Network grants are left empty. This case checks the native Kubernetes adapter without downloading another benchmark.

`tests/test_native_k8s.py` checks path and object configurations at task and sample level. It verifies that existing services, volumes, environment settings, resource limits, probes, extra resources and network grants are preserved. It also renders the resulting values with the real provider's Helm chart. Custom charts and other sandbox providers remain outside this adapter.

At `1da184c9c6018d37794957f8153ad863444e9030`, the native fixture passed on Hawk ([run](https://viewer.hawk.hawk.generalitylabs.ai/eval-set/ia-api-free-native-k8s-20-1zjbdw4793cpfcj9)): staging, benchmark shell, real grading, verdict and submission all passed. The grade was `C`, and the only model recorded was `ia_probe/driver`. Native logs and the summary are under `out/compatibility-native-k8s-20261022/`. The ordinary suite passed 197 tests; both existing Docker investigation lifecycle variants passed separately; type checking passed.

## Add another eval

Add a `*.eval-set.yaml` file under `tests/compatibility/cases`. There is no Python benchmark allowlist or fixed case count. Ordinary CI automatically validates every case against Hawk’s configuration model and checks its mock response fixtures. The heavy runner discovers the same directory and installs each case’s declared packages on Hawk.

Declare the benchmark package at a pinned revision, its registered task, the source module to import, task arguments, sample selection, and any required environment or secrets. Copy an existing case with a similar environment. The probe runs the production investigator tools and the benchmark’s actual setup and scorer. An unsupported benchmark reports the stage that failed.

For a tool-calling judge, put its exact canned response in the case specification:

```yaml
mock_judges:
  grader:
    tool: submit
    arguments:
      grade: INCORRECT
      explanation: Scripted mock judgment; no answer verification.
```

For a text or structured-JSON judge, use `content` instead:

```yaml
mock_judges:
  grader:
    content: '{"extracted_final_answer":"MOCK ANSWER","reasoning":"Scripted mock judgment","correct":"no","confidence":100}'
```

The grader still builds its own prompt and validates the reply. Unknown tool names, malformed fixtures and unscored judgments fail the test. Mock fixtures are per model role, so different roles may use different reply formats.

Run `uv run pytest tests/test_compatibility_cases.py` for cheap validation, then `uv run python tests/compatibility/run_suite.py --cases NAME --output out/NAME` for the real Hawk probe. The ordinary checks do not download every external benchmark. The heavy run is the package-installation and execution check.

## Score validation

The grade tool returns Inspect's complete `Score` record, including metadata and failure reasons. Compatibility checks validate that record with Inspect's `Score` model. A `grader_failed` or `scoring_failed` reason fails the grading stage. A wholly unscored result also fails. Optional empty metric groups may contain NaN when other groups have actual scores, as MirrorCode does; that is not itself a grading failure.

Mocked grading is marked `passed_mock`, and the native log must contain the declared mock model call. An undeclared external model call fails the run. These checks test execution and parsing, not the correctness of the canned judgment.

## Publishing checks

The default pytest selection excludes both `docker` and `latex` tests. Heavy publishing checks run in CI, not on the operator's machine. The dedicated `LaTeX fixture` job checks full registry regeneration and an actual PDF build from the shipped template. No model calls are made.
