SMOKE TEST. You are testing Inspect Auditor itself, not auditing this benchmark. Keep total spend under $1 and every job to one or two samples.

Push every button once, in this order, and keep going when one fails:

1. In your box: confirm the benchmark is installed (/inputs/benchmark-install.json), import its task, build it, load its dataset, look at one sample.
2. `budget()`; `register(view="summary")`.
3. The jobs this case lists below, each on ONE sample: `run_eval` (fresh attempts, `rescore`, `code` and `packages` where the case says), `send_investigators`.
4. `jobs` with every action: list, status, wait, log, collect. Collect everything.
5. `register` views again, `budget()` again.
6. Submit findings, then publish the report. Then stop: do not go on to audit the benchmark itself.

Your findings and report are about the tool, not the benchmark: what you did, what worked, what failed or misbehaved (with the exact error and the call that caused it), what was confusing, slow or missing, where you got stuck and how you got round it, and what each step cost. One finding per problem. If something blocks the routine, say exactly what and use report_blocker only when nothing else can be tested.
