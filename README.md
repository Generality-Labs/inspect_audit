# inspect_audit

An automated tool for auditing evals. It has two parts:

- **The agents.** Inspect tasks that audit a benchmark with LLM agents: `inspect_audit/audit` (the sample auditor) and `inspect_audit/investigate` (the investigator, which publishes a report).
- **The findings store.** `inspect-audit-findings` runs deterministic checks over evals, collects their findings with the agents' into a store, takes review decisions, and renders an export for a table site.

Leads carry the store's findings to the investigator, and an import carries the investigator's results back. See [docs/](docs/README.md); new to the findings store, start with the [end-to-end walk-through](docs/end-to-end.md).
