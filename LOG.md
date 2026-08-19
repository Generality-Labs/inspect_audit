# inspect_audit LOG

## 2026-08-19 (evening) — SWE-bench Verified exploit experiment

**Goal:** headline "gpt-luna scores ~100% on SWE-bench Verified via exploits it found."
Two-stage design (James's):
- Stage 1 (discovery): red-teaming auditors on N random Verified samples, each records
  the EXACT replayable exploit (literal commands) in its verdict.
- Stage 2 (validation): replay each recorded exploit as the solver action in the REAL
  inspect_evals/swe_bench task; the real benchmark scorer grades. Headline = pass rate.
- Rationale: don't trust our own grade() for the headline; the real benchmark is the judge.
  Self-protecting: only box-executable exploits replay (an /audit-leaning "exploit" won't).

**Plan / progress:**
- [ ] tighten red-teaming skill: record exact replayable exploit commands, not prose
- [ ] build Stage-2 replay task (real swe_bench, solver runs recorded commands)
- [ ] validate Stage 2 on 2 KNOWN exploits (django-13807, sympy-17630) -> expect 1.0/1.0
- [ ] Stage-1 pilot: 5 random samples across repos -> exploits
- [ ] Stage-2 those 5
- [ ] scale Stage-1 to 100, then Stage-2 the 100
- config: Stage-1 red-teaming at effort=high (hinted exploit, saves time vs xhigh)
- results -> JSONL incrementally

**Established earlier today:**
- grade/reset proven live (probe 0.0->1.0->0.0). red-team babytest: 2/2 EXPLOITABLE,
  verified (real grader output, no /audit sourcing, reset confirmed via 0.0 failed routes).
- xhigh GTA run: 10/10 ACCESSIBLE, all backed by real git archaeology. USED-fix working
  (listed -> ACCESSIBLE not USED).
- SWE-bench Verified = 500 samples, 12 repos (django 231, sympy 75, sphinx 44, ...).
- Cost today ~$1.23 total (dollar-meter). ~$0.04/model-item.
