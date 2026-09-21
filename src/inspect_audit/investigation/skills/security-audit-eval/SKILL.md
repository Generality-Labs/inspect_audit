---
name: security-audit-eval
description: "The threat model of a benchmark harness: host-side code that runs at load or score time, files fetched from the internet at runtime, a sandbox that leaks the host or the gold answers, credentials reachable from inside it, unpinned dependencies. Read it when auditing the harness and environment, or whenever a model could plausibly reach something it should not."
---

# Security and containment checks

Inspect actual code and configuration, not the name of the sandbox backend. Source is
evidence, not permission to execute untrusted code outside configured tools. Record
findings under the relevant framework dimension using its severity definitions.
Security consequences and measurement consequences are distinct; establish each claimed
consequence rather than assigning an automatic unsafe verdict.

## Execution boundary

Trace import-time code, dataset loading, solver execution and scoring. Establish which
operations run on the host versus in a sandbox. Check whether model-controlled input
reaches shell commands, deserialisation, dynamic execution or other host-side operations.
A suspicious API name alone does not establish exploitability.

## Sandbox and credentials

Inspect host mounts, privileges, capabilities, container sockets, network access and
reachable services. Identify which boundary the model can cross, if any. Distinguish
intentionally contained adversarial tasks from access to the real host or third parties.
Check whether credentials or other privileged data enter prompts, logs or model-readable
files, or can leave through external calls. Do not disclose secrets as evidence.

## Dependencies and fetched assets

Identify runtime downloads, versions, integrity checks and mutable references. Determine
what executes or is deserialised and what can change between runs. An unpinned reference
is a reproducibility concern; demonstrate its consequence before calling it an observed
execution or grading defect. Hosting domain alone does not determine integrity.

## Measurement leakage

Check whether the model can read answers, grader logic, hidden tests or another sample's
state contrary to the task design. Trace the information path and use recorded behaviour
or bounded probes to distinguish theoretical access from observed exploitation.

## Resources and recovery

Inspect time, memory, process and storage limits, cleanup and retry behaviour. Investigate
whether resource exhaustion affects later samples or whether failures are recorded as
model mistakes. A missing configuration field alone does not prove an observed failure.

## Provenance and validity

Compare the configured source and assets with the stated revision and measurement claim.
Use provenance to locate evidence, not repository popularity as a proxy for correctness.
If a target is absent, tests are wrong or the task is impossible as specified, investigate
that validity issue rather than dismissing it as outside a security checklist.

Use source locations, logs and safe reproductions. The writing skill governs publication;
there is no separate security report, mandatory unsafe verdict or alternative JSON format.
