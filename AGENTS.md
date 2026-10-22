# AGENTS.md

Working rules for coding agents in this repository. Read this before a substantive change. Where code and this file disagree, raise it; do not quietly pick one.

## What the auditor is

The audit is the benchmark's own Inspect task with the auditor in place of its solver. Inspect sets each sample up exactly as it did for the evaluated agent (their files, setup script, setup solvers, boxes, target, scorers). We add one box beside theirs for the auditor, and replace only what must be replaced.

| Module         | Owns                                                                                                            |
| -------------- | --------------------------------------------------------------------------------------------------------------- |
| `_task.py`     | `audit_task`: declared inputs, `task_with(theirs, ...)`, every override, loud refusals                          |
| `_compose.py`  | their compose plus the `auditor` service, merged in Python with their relative paths made absolute              |
| `_k8s.py`      | native built-in-chart Kubernetes values plus the auditor service; preserves provider options and network policy |
| `_auditor.py`  | the solver: stages the item and source into the auditor's box, binds tools to the live sample, runs react       |
| `_grade.py`    | `grade`: a copy of the live state with the transcript under test, handed to their scorers                       |
| `_reset.py`    | `reset`: force-recreate their boxes, lay files and run setup again (Docker only)                                |
| `_logs.py`     | the declared logs: header checks, the attempt index, host-side reading tools                                    |
| `_verdicts.py` | checks as skills, `record_verdict`, `submit`, one scorer per check                                              |
| `_image.py`    | `view_image`: an image in a box (the auditor's or the investigator's), SVGs rasterised there                    |
| `_source.py`   | the benchmark's code for the auditor: the task's package followed by import, data files, install specs          |
| `_regrade.py`  | standalone concordance: Inspect's `score_async` on the declared logs, diffed against the recorded grades        |

## What the investigator is

One Inspect task: a react agent in its own box, launching benchmark and audit jobs. It runs wherever Inspect runs it (a Docker container locally, a pod on Hawk); only its jobs' backend differs.

| Module                        | Owns                                                                                                          |
| ----------------------------- | ------------------------------------------------------------------------------------------------------------- |
| `_investigate.py`             | `investigate`: declared inputs staged on the host, the job tools, the budget tool, the task                   |
| `dispatch/`                   | jobs and where they run (`local`, `hawk`), `policy` and the authoriser, `packages`, `spend`, the key launcher |
| `_log_sources.py`             | every way of naming logs (path, `hawk:`, URL) resolved to local files                                         |
| `_investigation_workspace.py` | staging inputs into the box, reading the report back out, publishing to Hawk's artifacts                      |
| `_report.py`                  | launched-job records, log provenance, operator turn-taking and outcome scoring                                |
| `_framework.py`               | analytical definitions and rubric, without a separate judgment store                                          |
| `_findings.py`                | editable draft, trusted submitted bundle, manifest and checkpointed phase                                     |
| `_publishing.py`              | native dynamic tool source, publisher packages and standalone `publish` task                                  |
| `publishers/latex.py`         | default pinned GL audit template, generated structure and host/image TeX compilation                          |
| `publishers/gl.py`            | reviewed GL components and static HTML/PDF output                                                             |

The supported shared-budget setup routes calls through one capped OpenRouter key (the assigned Hawk user's key on Hawk). Inspect prices individual sample limits; there is no investigation-wide spending ledger or fixed reporting reserve. Resume is Inspect's checkpointing of `/workspace`, `/inputs` and the store.

Launch configuration is resolved once into an immutable `LaunchPolicy`. Its snapshot and SHA-256 are saved in Inspect's sample store; resume must match the original policy and investigator model. `/inputs/policy.json` is a readable copy, never authority. Child submissions pass through `InvestigationAuthorizer` and record the policy digest. The launch policy permits per-sample audit cost and concurrency limits; it does not implement a shared dollar ledger. Unsupported policy fields are refused.

Structured YAML accepts `benchmark`, `audit`, `policy` and `execution` sections (see `examples/investigation-policy.yaml`). Restricted Hawk children require the new restricted-credentials/transcript-input API; they never receive a user refresh token. Submission authority currently remains in the investigator's host process. A separate trusted launcher and moving benchmark enumeration out of that process remain work; the policy object alone is not a process-isolation boundary.

## Load-bearing rules

1. **Declared inputs only.** The benchmark task and its arguments, the sample ids, the checks and the logs are passed in. Nothing is inferred from log headers, the registry, the filesystem or naming conventions. If the auditor needs something it was not given, it refuses and says what to declare.
2. **One attempt key: (eval_id, sample_id, epoch).** The same key in two logs is an error. Never pick between inputs.
3. **Logs must match the declared run.** Every header must record the declared task, the declared arguments (and no undeclared ones) and the installed benchmark version. A mismatch is an error, not a warning.
4. **Their task, our solver.** Their services keep their names; their `default` stays the default. Ours is `auditor`. Never rename or rewrite their services.
5. **Their grader runs only when the auditor calls `grade`.** The task's scorers are ours. Their scorers judge with model roles bound from the logs or declared; a scorer that judged with the evaluated model itself is refused.
6. **Override, never inherit.** Every limit, the generate config, epochs, metrics and approval of theirs is replaced explicitly in `audit_task`.
7. **Fail loudly.** Unsupported inputs raise with the reason. A value the source never recorded is unknown, never a default.
8. **Private Inspect imports in the auditor live in `_reset.py` only.** Everything else uses Inspect's public API. If you need an internal, the answer is an upstream request, not an import.
9. **Logs never enter a box.** They are read on the host. Only what the auditor asks to export is written, and only into its own box.

## Unsupported, on purpose

- Benchmarks that prepare their environment inside their solver (SWE-Lancer's `chain([setup, react])`): swapping the solver skips the preparation. Not detectable; documented, not handled.
- Selections containing different per-sample sandboxes. One shared sample sandbox is supported; its declaration is merged with the auditor service at task level.
- `reset` on k8s.
- Custom Kubernetes Helm charts. Native values for the built-in chart and Kubernetes declarations using Compose are supported. Native values retain the declared network policy, so the auditor may be offline.
- The evaluated agent's own tools. The auditor works through `benchmark_bash` and `grade`.

## Tests

- Test behaviour against real Inspect, real logs and real Docker. A test that only checks our code agrees with itself measures nothing.
- `tests/test_audit.py` runs a real audit of `tests/fixtures/toy_benchmark` with a scripted mockllm auditor. Docker tests are deselected by default: `uv run pytest -m docker`.
- No model spend in tests. Paid validation runs are separate and need an explicit go.

## Findings handoff

The investigator can edit its draft through Bash/Python. Collected auditor records remain host-authoritative. `submit_findings` validates and freezes the bundle; publishing tools and skills are withheld until this boundary. Afterwards job tools disappear and host launch code refuses new experiments. There is no reopening. The same agent continues, or `inspect_audit/publish` starts a fresh publishing run from the saved bundle. See `docs/findings.md`. Do not introduce a second mutable report findings format into this workflow.

The default latex publisher downloads and verifies the pinned audit_reports template on the host; investigation uses generated analytical registry data. No template copy is vendored. Publisher validation runs before freezing findings. Agent-authored publishing input is prose only; grades, criterion assignments, counts and evidence remain in the frozen findings.

The optional GL web publisher draws through pinned website components. Agent calculations may prepare data from existing evidence; agent-written drawing code is not an input. The optional Playwright dependency and Chromium browser run on the Inspect host. CI exercises actual Docker handoff and HTML/PDF publication without model spend.
