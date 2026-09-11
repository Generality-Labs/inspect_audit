# inspect_audit

An automated tool for auditing evals. An investigator agent reads a benchmark's source
and logs, runs experiments and per-sample audits on Hawk, and publishes a report.

```bash
inspect eval inspect_audit/investigate -T config=investigation.yaml --model openrouter/openai/gpt-5.6-sol
```

`make check` lints and type-checks; `make test` runs the suite without Docker; `make test-docker` runs the rest.
