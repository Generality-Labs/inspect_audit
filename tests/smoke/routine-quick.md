QUICK SMOKE TEST. You are testing Inspect Auditor itself, not auditing this benchmark. Keep total spend under $0.50. Do exactly this, once each, and nothing else (no probing, no extra jobs):

1. `register(view="summary")`.
2. `run_eval` rescoring ONE sample of the supplied logs with the judge the policy permits, epochs 1.
3. `send_investigators` on that sample, check gold-answer, with the supplied logs, item_cost_limit 0.25.
4. `jobs` wait and collect both.
5. Submit findings: one finding per thing that failed or misbehaved (exact error and call), or none. Then publish the report.

If a step fails, record the exact error and go on to the next.
