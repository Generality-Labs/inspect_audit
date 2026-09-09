# The investigator

The registered `inspect_audit/investigate` task reads a benchmark's source and
supplied Inspect logs, analyses them in a Docker workspace, and, when `hawk_api_url`
is set, runs things on Hawk: the agent writes an ordinary Hawk eval-set config (the
benchmark itself, or inspect_audit's sample auditors over recorded attempts) and
`hawk_submit` checks it against a policy and submits it; `stage_logs` makes the supplied
logs readable by a job; `jobs` waits, collects and accounts. It writes a six-section Quarto HTML report and exits by default;
explicit interactive mode waits through ACP.

## Remote work

The agent's shell runs in a container with no credentials. The dispatch tools run in
the Inspect process on this machine and use the `hawk` CLI (your login, in its keyring)
and, for staging the supplied logs once at setup, your AWS profile.

A submitted config is treated as hostile input. It is parsed with Hawk's own schema and
then checked as the thing that will actually run: allowlisted packages (the task package
and inspect_audit only), task registry names, models, images, secrets
(`OPENROUTER_API_KEY` only) and runner environment keys, with the same rules applied to
task arguments, where a `model`, an `image`, a size or a log source can otherwise be
overridden past the outer fields. Task-level secrets, isolation, runner image, cpu,
memory, agents, solvers and unknown keys are refused. `epochs`, `token_limit` and
`time_limit` are required and capped, and a null `limit` is not a size.

Spend is bounded, not estimated. Every config states `cost_limit`, the dollars one
sample may spend, which Inspect enforces inside the runner using prices stamped in at
submission from the same registry the local allowance uses. The job holds
`cost_limit x samples x models x epochs` against the shared allowance from submission
until `jobs(action="collect")` downloads the logs (to `/inputs/jobs/<label>/`, read-only
in the box) and records what it really cost. If a model in a collected job has no
registered price the cost stays unknown and the reservation stands, rather than an
estimate being written down as a measurement. The check and the record happen in one
locked ledger transaction, so two submissions in flight cannot both take the last of the
allowance.

The eval set id is assigned at submission, one per job: a reused id makes Hawk resume
that set. Supplied logs stay at the prefix they were staged to and are read through the
Hawk API, so any job can read them whatever its own id is. Every accepted submission is
saved under `<investigation>/jobs/` and recorded in `jobs.json`, written as `pending`
before the CLI call, so a submission whose response is lost is reconciled against Hawk
rather than lost or sent twice. `resume=<investigation dir>` carries on in an existing
directory: same inputs, workspace, journal and ledger, no restaging, pending jobs
reconciled first. Worker models are restricted to `worker_models`.
Models are routed straight to OpenRouter with `OPENROUTER_API_KEY` from `secrets_file`;
the middleman is bypassed with `HAWK_RUNNER_REFRESH_URL: ""`. Required with
`hawk_api_url`: `task_package`, the git spec Hawk runners install to run the audited task.

## Run

Install the package in your Inspect environment and start Docker. Model credentials
belong to the local Inspect runner, not the research container. Choose any model
supported by that environment (including an already configured Middleman route).

```bash
inspect eval inspect_audit/investigate \
  -T repo=/absolute/path/to/benchmark-repo \
  -T logs='["/absolute/path/to/benchmark-logs", "/absolute/path/to/audit-logs"]' \
  -T overview="Investigate scoring reliability and failed runs" \
  -T output_dir=/absolute/path/to/investigations \
  -T budget_usd=10 \
  --model YOUR_MODEL --display plain

# For an interactive run add: -T interactive=true --acp-server
# Then attach from another terminal with: inspect acp
```

`repo` also accepts an HTTPS Git URL. Optional arguments: `revision`; `paths` (repository
paths to include, so the snapshot holds the task under audit rather than every eval in a
collection); `target_task`; `paper` (a local file, or a URL downloaded at setup, arXiv
abstract pages resolving to their PDF); `docs` (documentation directories mounted read-only
at `/inputs/docs/<name>`, e.g. the Inspect docs and Hawk's, which the agent is told to read
before using the APIs); `extra_skills` (skill directories); `token_limit` (none by default).
Local repositories are snapshotted with `git archive` at HEAD or `revision`: dirty
files, untracked files and submodule contents are excluded. Remote cloning is done
by the investigator in its sandbox; it records the resolved commit. Read access to
private HTTPS repos is not configured automatically.

Batch mode is the default and exits after publication. Use `-T interactive=true`
with `--acp-server` to opt into discussion. In interactive
mode publication is followed by an ACP input wait; attach with `inspect acp` and
decline/cancel the input request to end the discussion. The process must remain
alive to retain the same live session. Automatic crash recovery is not yet wired.

## Artifacts and limits

Each invocation creates a unique directory under `output_dir`, recorded in the
Inspect task metadata as `investigation_dir`. `inputs/` contains selected inputs
and the seed manifest; the container mounts it read-only. `work/` is a persistent
writable workspace. `published/<version>/` contains the Quarto source, rendered
HTML, a snapshot of the findings register, figures and any evidence the investigator included. Published
versions are outside the container's mounts. The agent cannot overwrite them.

The image installs Inspect, pandas, pyarrow, matplotlib, Jupyter, PDF text extraction
and Quarto 1.9.38. The first build downloads dependencies. No host Docker socket,
home directory or provider credentials are mounted into the research container.

`budget_usd` is enforced by default through Inspect's cost limit, covering this
investigator's own model calls (child jobs, when they exist, will be budgeted separately).
OpenRouter's current prices are registered at setup so any `openrouter/...` model has a
price; for other providers supply `--model-cost-config`. Set `-T enforce_cost_limit=false`
to make it a planning number only. The `budget` tool shows spend by model and says
"unknown" rather than zero when a model has no price. There is no token cap unless
`token_limit` is set.
A 500k **output-token** limit (including reasoning, excluding repeated/cache-read
input) applies independently and can
be overridden through Inspect. Limits include follow-up conversation; reaching one
can end the session before publication, but the draft workspace remains on disk.
Docker/storage costs are not included. This is not a global or provider-enforced cap.

The report follows the editorial structure of
`Generality-Labs.github.io/blog/posts/simpleqa-audit-v2/index.qmd`: opening
assessment, The benchmark, How models respond, Issues, Bottom line. Its site-only
JavaScript/assets and benchmark findings are not copied. Components enforce simple
chart styling and escape transcript HTML; editorial judgement remains in the skill.

There are two working memory artifacts: `work/journal.md` and
`work/report/findings.json`. Publication validates the register against the bundled
schema and checks evidence file paths. Supported/qualified findings require evidence;
ids must be unique. Evidence must resolve inside the report or `/inputs`; referenced
inputs are copied into the published bundle and that snapshot's paths are rewritten.
Location strings and quotes are not automatically verified against .eval events yet.

Rendering checks that HTML can
be built; it does not verify findings. Independent verification/rubric grading and
Hawk orchestration remain separate future changes. Extra skills cannot enable tools
that this task does not provide.

## Validate without model spend

```bash
uv run pytest tests/test_investigate.py -m 'not docker'
uv run pytest tests/test_investigate.py -m docker
```

The Docker tests read a staged Git snapshot, create a chart, render Quarto, and
verify the exported HTML after sandbox cleanup. A second test drives the actual
agent loop with scripted model responses to load a skill, read real Inspect logs,
check the budget and publish. They do not measure the investigator model's quality
or exercise a live ACP client; the discussion transition is tested separately.

## Remaining boundaries

The outer investigator is local-only. Task construction still creates the workspace
before evaluation starts, and reconstructing the task creates a new directory.
Log files are hardlinked into read-only inputs on the same filesystem, with copying
only across filesystems; use completed immutable logs, since hardlinks share changes
made by another host process. Resuming is explicit: pass `resume=<investigation dir>`;
there is no automatic retry.
The research container uses a per-run Compose network with internet egress.
Its Inspect image install still uses the host version; a development-only host build
needs an explicit image strategy before this outer task is moved to Hawk.

The older `inspect_audit/report` interactive log session remains compatible. Shared
publication, findings validation and ACP input handling now live in `_report.py`;
`investigate` adds source orientation and autonomous work. This is not yet a unified
replacement for every report entry point. Rediscovery input/web exposure policy must
be selected before a graded run; no graded run is launched by this command.
