# inspect_audit

An automated tool for auditing evals: benchmark validity auditing for Inspect AI evals.

It has two layers:

- **The investigator** (`inspect_audit/investigate`): one agent that reads a benchmark's source, paper and logs, commissions work on Hawk, and publishes a report in the Generality Labs LaTeX template (`report.pdf`, `findings.json`, `coverage.json`, `assessments.json`).
- **The item auditor** (`inspect_audit/audit`): one agent per benchmark item, in a box with the item, its recorded attempts and the benchmark's own scorer. It checks the gold answer, the answer format, the specification and anything else it finds, and labels each item. The investigator runs these as Hawk jobs.

## Running an investigation on Hawk

Write a Hawk eval-set config whose task is `investigate`:

```yaml
name: my-audit
packages:
  - inspect_audit[remote] @ git+https://github.com/Generality-Labs/inspect_audit.git@<commit>
  - git+https://github.com/<org>/<benchmark>.git@<commit>
tasks:
  - package: git+https://github.com/Generality-Labs/inspect_audit.git@<commit>
    name: inspect_audit
    items:
      - name: investigate
        args:
          repo: https://github.com/<org>/<benchmark>.git
          revision: <commit>
          target_task: <benchmark>/<task>
          logs: [hawk:<imported eval set id>]    # logs brought in with `hawk import`
          hawk_api_url: https://api.hawk.hawk.generalitylabs.ai
          worker_models: [openai/gpt-6-luna, openai/gpt-5-mini]
          artifact_dir: s3://<bucket>/evals/my-audit/artifacts
          budget_usd: 20
          overview: <what the benchmark is and what the supplied logs are>
          instructions: <what you want audited>
          required_coverage: 1.0                 # optional: share of items the report must assess
models:
  - package: openai
    name: openrouter
    items:
      - name: openrouter/openai/gpt-6-luna      # the investigator, through Middleman
runner:
  secrets:
    - name: INSPECT_AUDIT_OPENROUTER_API_KEY
limit: 1
cost_limit: 20
retry_attempts: 0
```

and run it with your OpenRouter key in a dotenv file:

```bash
hawk eval-set run my-audit.eval-set.yaml --secrets-file <file with INSPECT_AUDIT_OPENROUTER_API_KEY=...>
```

The investigator's own calls go through Hawk's Middleman. The jobs it submits go straight to OpenRouter on your key (`provider: openrouter-direct`, the default); `provider: middleman` routes them through Hawk instead.

`worker_models` are the models the investigator may run. Name them as OpenRouter ids (`openai/gpt-6-luna`, not Middleman's `openrouter/openai/gpt-6-luna`), and each must also be a model Middleman knows, because Hawk looks up every model a job names in Middleman before it accepts the job.

Before the investigator's first model call, the run checks:

- that every worker is an OpenRouter id;
- each worker, with one real OpenRouter call made the way a job will make it;
- that every supplied log source is readable.

A worker OpenRouter refuses, or a broken harness, stops the run there for nothing. An unreadable source is recorded for the investigator. Every job the investigator submits is validated first: Hawk's schema, the investigation's policy, and our own tasks' argument rules.

The report lands in `<artifact_dir>/investigation-epoch1/published/<id>/`. Reports cite logs by Hawk address and sha256 rather than copying them, and nothing is written anywhere Hawk would import into its warehouse.

Every argument is documented on `investigate` in `src/inspect_audit/_investigate.py`. `execution: local` runs the investigator in local Docker instead of on Hawk.

## Auditing items directly

```bash
inspect eval inspect_audit/audit -T task=<benchmark>/<task> -T logs=<log file or dir> --model <model>
```

## Findings

`inspect-audit-findings` turns eval logs and audit output into findings records; see `docs/finding-schema.md` and `docs/finding-schema-envelope.md`.

```bash
uv run inspect-audit-findings --help   # run, summary, hawk-sets, hawk-pull
```

## Development

```bash
uv sync                     # Python 3.13 via .python-version, so the Hawk extra installs
uv run pre-commit install   # optional: run the lint stack on every commit
uv run pytest               # the docker suite is deselected by default
uv run pytest -m docker     # container tests; needs a running Docker daemon
uv run basedpyright src
```

Linting (ruff, [zizmor](https://docs.zizmor.sh/), mdformat) runs via [pre-commit](https://pre-commit.com); CI runs the same stack plus basedpyright and pytest via the shared [`python-ci`](https://github.com/Generality-Labs/python-project-template) reusable workflow.
