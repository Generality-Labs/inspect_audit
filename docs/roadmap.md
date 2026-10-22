# Roadmap

## 1. Configure the tests

Keep ordinary tests, real-container tests and external Hawk compatibility probes separate. PRs run ordinary checks and a small real-container lifecycle gate. Main runs the full Docker suite. External probes retain native logs and report what executed, including failures and mocked judges.

## 2. Trim the package

Remove the standalone deterministic findings pipeline and its CLI, schemas, tests and obsolete documentation. Keep the live investigation register, auditor tools and launch policy. Remove dependencies only when the retained package does not use them.

## 3. Iterate until the execution tests pass

Use Inspect primitives, fix general integration failures, and rerun the affected benchmark cases. SimpleQA, SimpleQA Verified and HLE may use explicit mock judge responses to exercise grading integration without model spend. Mark those judges as mocked. Resolve Harbor's dependency and environment setup. CORE-Bench's native Compose requirements remain tracked in #59.

## 4. Redo findings

Review the actual auditor verdicts, investigator reviews and report authoring flow. Settle one structured findings contract before changing storage or report generation. Explanations, supporting evidence and review history must survive.

## 5. Clear old branches, PRs and issues

Delete branches only once their work is preserved in main or explicitly retired. Review unique work before closing its PR. Reconcile issues against the current implementation.

## 6. Take stock

Record the package size, module ownership, documentation, test coverage and known unsupported inputs. Distinguish execution compatibility from audit accuracy.

## 7. Review use of Inspect primitives

Have subagents read the Inspect skills first, then review the implementation for native APIs, unnecessary scaffolding and simplification opportunities. Ground proposed changes in current Inspect code and documentation.

## 8. Examine skills and live behaviour

Read the auditor and investigator skills, run small investigations, and inspect transcripts for experiments, confusion and useful findings. Review Laurence's PRs and branches to identify the changes that belong in this workflow. Measure accuracy against human labels after execution is reliable.
