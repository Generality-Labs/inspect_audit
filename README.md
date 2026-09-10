# inspect_audit

Investigate whether an Inspect benchmark's scores support its measurement claims.
The primary workflow is `inspect_audit/investigate`: a local Inspect agent reads a
repository and optional logs, commissions benchmark or per-item audit jobs on Hawk,
and publishes a versioned HTML report with evidence.

```bash
uv pip install -e '.[dev,remote]'
inspect eval inspect_audit/investigate -T config=investigation.yaml --model <model>
```

See [investigation setup](docs/investigation.md) for configuration, credentials,
remote logs, budgets and resuming a workspace. Remote dispatch needs Python 3.13+
and Hawk; the investigator's analysis sandbox needs Docker.

## Entry points

- `inspect_audit/investigate`: repository-to-report investigation, with optional Hawk jobs.
- `inspect_audit/audit`: per-item checks over an Inspect task and optional recorded attempts.
  Select checks with `items`; results are structured verdicts in Inspect logs.
- `inspect_audit/report`: legacy conversational synthesis over audit logs. Retained for
  existing ACP/frontend clients; it does not implement the investigation publication workflow.

The optional [frontend](frontend/README.md) is an ACP chat client. Inspect View shows
run transcripts. Neither is required for a batch investigation.

## Development

`make check` runs lint and types; `make test` runs tests without Docker;
`make test-docker` exercises real sandbox paths. Provider smoke tests are separately
opt-in. [Code inventory](docs/code-inventory.md) records the current size, compatibility
surfaces and custom integration that remains to simplify.
