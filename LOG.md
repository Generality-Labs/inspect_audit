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
- [x] tighten red-teaming skill: exact replayable `commands`, not prose
- [x] operator-notes insertion (general skill + per-run hint knob), threaded through
- [x] build Stage-2 replay task `swebench_replay` (real swe_bench, sandbox_type=k8s)
- [~] reviewer subagent checking implementation (a5fa629) -- awaiting (GATE 1)
- [x] Stage-2 validation (stage2-validate-gdwn7z621donvpz2): django-13807=1.0,
      sympy-17630=1.0 from the REAL benchmark scorer. GATE 2 GREEN. Replay pipeline
      proven: a recorded exploit genuinely passes real SWE-bench, not just our grade().
- [x] sample selection (seed 20260819): sample100.json (100 across 11 repos, django 45
      sympy 17 sphinx 8 sklearn 7 astropy 6 matplotlib 6 xarray 5 ...), pilot8.json
      (8 spanning top repos). scratchpad/.
- [x] reviewer (GATE 1): found HIGH bug -- gold patch/test_patch in /audit/sample.json
      lets a red-teamer launder the answer as a "box-mined" exploit that replays clean
      in Stage 2 (false positive vs the real claim). FIXED: redact ANSWER_METADATA
      (patch/test_patch/FAIL_TO_PASS/PASS_TO_PASS) from staged sample.json; grader
      unaffected (uses benchmark_metadata). 30/30 tests pass. Other findings: reset
      doesn't undo a `git commit` (MEDIUM, self-corrects in Stage 2 -- conservative);
      denominator framing (LOW, state explicitly); both noted not blocking.
- [~] Stage-1 pilot: redteam-pilot8-ph94tzy1pldyfkpl (8 across repos, effort=high,
      notes=git steer) -- running. extract_exploits.py pulls {id: commands} on done.
- [x] extractor bug caught+fixed (commands is a list, grade key varies) -> 7/7 captured
- [x] Stage-2 PILOT replay (stage2-pilot8-p6m1d7m5e54n3fbs): 7/7 pass the REAL benchmark,
      across 7 repos (astropy django matplotlib requests xarray sklearn sympy).
      PIPELINE PROVEN end-to-end across the repo spread. GO for 100.
- [~] Stage-1 100 (redteam-100-ar887fr74igw21ym): running. watcher b27yfpc90 extracts
      exploits on done. sample100.json (seed 20260819), effort=high, notes=git steer.
- [x] Stage-2 100 (stage2-100-arzcxgh1bzzgcar1): 89/91 replayed exploits PASS the REAL
      swe_bench scorer (97.8%). 2 fails (django-11206, sphinx-8548) = our grade() false
      positives that Stage 2 correctly caught (two-stage design working).

## RESULT (2026-08-19 ~20:35)

Random sample of 100 SWE-bench Verified (seed 20260819):
  100 sampled -> 98 completed audit -> 93 EXPLOITABLE (our grade) -> 92 replayable
  captured -> 89 pass the REAL benchmark scorer.
  => ~89% of randomly-sampled SWE-bench Verified is verified-exploitable via the
     git-history leak (gold fix recoverable from the container, real grader credits it).
     95% CI ~82-94%. Extrapolates to the full 500.
  Repo spread near-total; sphinx-doc the lone weak repo (4/8 in Stage 1).
  HINTED run: notes handed the git-history route (measures vulnerability+execution,
  not spontaneous discovery). Cost ~$3 (auditor model); Stage 2 ~$0 (no model).
  Caveats: 2 samples didn't complete; 2 Stage-1 false positives caught by Stage 2.
  Key: james's OpenRouter key shipped to Hawk as a job secret (middleman bypassed) --
  consider rotating.
- [ ] Stage-2 those
- [ ] scale Stage-1 to 100, then Stage-2 the 100
- config: Stage-1 red-teaming at effort=high; notes hands the git-history route
- GATE: do not scale to 100 until reviewer clean AND Stage-2 validation = 1.0/1.0
- anti-spiral: if a stage fails twice, STOP and report, don't shotgun

**Established earlier today:**
- grade/reset proven live (probe 0.0->1.0->0.0). red-team babytest: 2/2 EXPLOITABLE,
  verified (real grader output, no /audit sourcing, reset confirmed via 0.0 failed routes).
- xhigh GTA run: 10/10 ACCESSIBLE, all backed by real git archaeology. USED-fix working
  (listed -> ACCESSIBLE not USED).
- SWE-bench Verified = 500 samples, 12 repos (django 231, sympy 75, sphinx 44, ...).
- Cost today ~$1.23 total (dollar-meter). ~$0.04/model-item.

## 2026-08-19 (21:05) — BixBench audit

Target: BixBench (concordia_evals/bixbench, Laurence's public repo
github.com/LaurenceWroe/concordia_evals@59336ec). Bioinformatics data-analysis
benchmark; precise quantitative answers (p-values, ranges, %). Logs on Hawk:
bixbench-full-908dnzz4n7hx5v9u (20 samples, gpt-4o-mini, 4/17 correct).

Items: gold-answer (= "ground truth wrong": grades INCORRECT/ALTERNATIVES),
insufficiently-specified (NEW: question determinacy / open analysis choices),
answer-format. Red-teaming to be ADDED at scale-up (needs the box; box confirmed
building in pilot).
- caught: ground-truth-wrong was a dup of gold-answer (INCORRECT) -> deleted.
- [~] pilot bixbench-audit-pilot-9gu6gucfmrwygba3 (3 samples, 3 passive items).
      concordia installed OK, task resolves, capsules downloading (box builds).
      watcher bd1v0idyd.
- PLAN (James): let pilot finish, THEN scale up + add red-teamer (unhinted first --
      BixBench exploit route unknown, maybe answer in capsule notebook/data).

## 2026-08-19 (21:26) — BixBench FULL audit LAUNCHED
bixbench-audit-full-23pebht4d4fs0yfh: all 205 samples (train, only split),
4 items [gold-answer, insufficiently-specified, answer-format, red-teaming(unhinted)],
effort=high, token_limit=10M, judge_model=openrouter/openai/gpt-4o (keeps judge on
OUR key, not Hawk's OpenAI -- default was bare openai/gpt-4o). watcher buy373cn0.
Cost est ~$100-200. Log coverage: only ~20 samples have attempt-logs (answer-format
weak on the ~185 without). Pilot found: bix-10-q5 UNDERSPECIFIED (auditor re-ran
chi-square=gold + Mann-Whitney=different -> question doesn't fix the test). Spend
reconciled exactly today: 83M luna tokens = $5.49 (dev/reconcile_spend.py).
Hawk-OpenAI luna spend is NOT ours (our middleman usage=0); someone else's bare-luna run.

## 2026-08-20 (11:39) — BixBench v2 (fixed) LAUNCHED
bixbench-audit-v2-s24yztzzkznh4o9n. Fixes over v1:
- logs -> imported-imported-bixbenc-wiepam15yi6bi450 (FULL 205-sample attempt log;
  v1 mistakenly used bixbench-full-908 = 20 samples).
- red-teaming skill REWRITTEN: EXPLOITABLE now = "object-level WRONG submission the
  grader accepts" (not "passing grade without work", which the gold satisfied ->
  the false 67/104). Allows using /audit + grading code; IMPORTANT: don't submit gold.
  Licenses adversarial attacks on the LLM judge (confuse/bribe/inject).
- retry_attempts=3 (k8s pod flakiness caused ~19 NO_VERDICT in v1).
- time_limit=3600/sample (per-sample, not total), token_limit=10M, judge=openrouter/gpt-4o.
v1 findings recap: insufficiently-specified ~77% UNDERSPECIFIED (VERIFIED, discriminates,
real recomputation) = the real result. gold-answer mostly grounded. red-teaming v1
contaminated (gold leak). v1 cost $7.20. watcher bj317pnuj.
KNOWN CAVEAT: tools still global (red-teaming's grade/reset visible to passive items);
answer-format fabricated PENALISED via grade probes in v1 -- watch if it recurs now
that real logs give it real casualties.

## 2026-08-20 (14:xx) — code review + fixes (Claude, pkg-level, not a run)
Read whole pkg + vendored skills + hawk/inspect docs. Fixed 8 issues, all tested,
ruff+mypy clean, 46 non-docker pass (was 28), helm-render test now actually runs.
- restore_benchmark: reverted order to git-restore-THEN-setup (was setup-then-clean,
  which wiped untracked files setup had just written). _sandbox.py.
- attempts join: was sample_id only -> foreign task's overlapping ids silently
  attached. Now filters on task_name (unqualified match). _audit.py attempts(task=).
- Dockerfile / bare-docker benchmark envs were silently dropped (only compose-file
  benches got a benchmark service). New benchmark_source() synthesises them the way
  inspect's own auto-compose does (network_mode:none kept). Warns when unreproducible.
- swebench_replay: base.dataset=/base.solver= assignment -> replay_task() via
  task_with (goes through Task normalisation). NB task_with MUTATES base in place +
  returns same obj; fine here (fresh base).
- grading.md {modules} multi-scorer-module bug: import_module('a b') -> per-module loop.
- test_values CHART path pointed at ephemeral scratchpad (silent skip forever). Vendored
  agent-env chart -> reference/agent-env-chart (commit 1dc1579, MIT). INSPECT_AUDIT_CHART override.
- _hawk_fetch filename collision across comma-joined sets -> prefix. mypy guards on
  audit_probe None-task + reasoning_effort cast + grade results dict typing.
- FOLLOWUP (14:56): benchmark_source now imports inspect's own COMPOSE_GENERIC_YAML /
  COMPOSE_DOCKERFILE_YAML / DOCKERFILE (from _sandbox.compose + .docker.config) instead
  of hand-rolled YAML -- synth box tracks inspect's auto-compose, fails loud (not silent
  drift) on upstream change. DOCKERFILE aliased INSPECT_DOCKERFILE (local same-name const).
  Behaviour-preserving: same test_sandbox.py assertions pass.
- Known-still-open (not touched, from prior log): red-teaming tools global/visible to
  passive items. HAWK_API_URL for Generality = https://api.hawk.hawk.generalitylabs.ai
  (NOT hawkbench.com = Epoch). Not yet persisted to ~/.config/hawk-cli/env (perm classifier).

## 2026-08-20 (15:xx) — bixbench-audit-v2 wedged + DELETED; k8s_sandbox write-hang diagnosed
- v2 (bixbench-audit-v2-s24yztzzkznh4o9n) deadlocked: 66 sandbox pods held, completed=0,
  hawk stop looping "interrupt all samples" 13:04->14:02+ unstoppable. DELETED (hawk delete;
  pods=0, logs preserved). Root cause via py-spy: 15/16 pod-op-executor threads blocked in
  ssl.send inside k8s_sandbox _pod/op.py:77 (_write_stdin_chunked -> ws_client.write_stdin).
  No socket I/O timeout (op.py:91 _request_timeout is establishment-only). BixBench CapsuleFolder
  zip write into benchmark container crashes it mid-write; crashed container restarts in-place so
  pod keeps its slot; write thread hangs forever; pool saturates; cooperative stop can't cancel
  C-level ssl.send. retry_attempts=3 amplified. Fix = write watchdog closing ws_client on deadline
  (turn hang->retryable PodError); carry patched k8s_sandbox via eval-set packages:.
- Repro launched to confirm KILL REASON (raw concordia_evals/bixbench, 3 samples, retries 0,
  cost_limit 1.0): bixbench-crash-repro-dmuez8v6gcrlix4e. Config in MY scratchpad
  bixbench-crash-repro.eval-set.yaml. Watching pods for last terminated reason (OOMKilled vs
  Evicted vs Error). NB dead-run logs showed last_reason "Error" (NOT OOMKilled) -> maybe not OOM.
  bixbench-audit-v2.eval-set.yaml (the real launch config) is in c69e... scratchpad.

## 2026-08-20 (16:5x) — bixbench k8s crash ROOT-CAUSED + FIXED + VALIDATED
Root cause (verified): concordia bixbench k8s entrypoint unpacked the capsule zip the
moment the file EXISTED (`until [ -f zip ]`), but inspect's Sample.files write creates the
file empty then streams bytes. Large capsules => entrypoint unpacks a TRUNCATED zip =>
unpack python exits non-zero => container main process dies (exec tail -f never runs) =>
crashloop (k8s reason "Error", NOT OOM/evict). Meanwhile inspect still streaming => write
hangs on timeout-less ssl.send => pod-op pool saturates => unkillable (the v2 wedge).
WHY LAURENCE OK, US NOT: verified from his log header — he ran sandbox_mode=DOCKER (capsule
volume-mounted, no in-pod write, no race), 205/205 success, imported to Hawk. We run k8s
(Hawk has no docker daemon) => hits the streamed-write path. avoid_images=True in BOTH => not
the cause. Small capsules (first 3 samples) write fast enough to dodge the race; large ones
(bix-16, bix-38...) always lose it.
FIX (1 line, concordia bixbench.py k8s entrypoint): gate unpack on the zip opening as a
complete archive: `until [ -f zip ] && python3 -c "import zipfile; zipfile.ZipFile('zip')" 2>/dev/null; do sleep 1; done`.
Central dir is written last => opens only when write finished. NO decompression => zstd-safe
(capsules use zipfile-zstd; testzip() would've hung on valid zstd zips — rejected that).
Pushed: jrh-mann/concordia_evals@fix/k8s-capsule-write-race (forked from LaurenceWroe).
VALIDATED on Hawk k8s, bix-16-q1 (the confirmed crasher): unfixed=benchmark restart 1 (crash);
fixed=restart 0, sample success (completed=1 errored=0). ~$0 (audit_probe, no model).
HAWK GAP found: status/watch API drops container lastState.terminated (no exitCode/reason/msg)
+ no per-container logs => k8s sandbox crashloops are near-undebuggable on Hawk (dev in docker).
STILL OPEN: (1) k8s_sandbox write watchdog/timeout (defense-in-depth: mid-write death ->
retryable error not immortal hang) -- upstream inspect_k8s_sandbox _pod/op.py:77. (2) PR the
concordia fix to LaurenceWroe. (3) hawk lastState/container-logs feature gap.

## 2026-08-20 (19:39) — bixbench-audit-v3 COMPLETE (fixed run, real result)
bixbench-audit-v3-9n5jhqgsz50h96rz. 205/205 completed, 0 errored, 0 benchmark crashes
(the concordia entrypoint fix held across the full run — v2 wedged here). luna/high auditor,
gpt-4o judge, 4 items, Laurence's logs (imported-imported-bixbenc-wiepam15yi6bi450) joined,
concordia FIX FORK (jrh-mann@fix/k8s-capsule-write-race). Cost ~$15 (openrouter daily
6.88->22.03). Preceded by 10-sample luna pilot (~$0.73, crash-free, verdicts reasonable).
RESULT (204 tallied at first read, 205 final; distribution stable):
- insufficiently-specified: UNDERSPECIFIED 76% / SPECIFIED 18% / BORDERLINE 5%
  -> REPLICATES v1's ~77%. Headline: 3/4 of BixBench questions don't pin a unique answer.
- gold-answer: ALTERNATIVES 62% / CORRECT 25% / INCORRECT 13% / UNVERIFIABLE ~0.
  Only 1/4 cleanly-uniquely correct; 13% gold outright wrong.
- answer-format: SOUND 87% / PENALISED 13%.
- red-teaming: RESISTANT 91% / EXPLOITABLE 9% (vs v2's contaminated 67/104 -- rewritten
  skill looks decontaminated).
CAVEATS: single-pass luna verdicts (~10% test-retest flip per README) -> aggregate is the
signal, 76%~=77% replication corroborates. v3 individual verdicts not yet spot-verified.
NEXT (offered, James to pick): materialize CSV+script; spot-verify INCORRECT-gold(26) &
EXPLOITABLE(18); done. Log at v3_log/ (scratchpad, 40MB .eval).

## 2026-08-20 (19:53) — v3 CORROBORATED against model scoring (audit grades PREDICT model acc)
Joined v3 audit verdicts to Laurence's attempt scores (gpt-5.6-sol, 205 samples, overall
42.9% acc). Materialized: dev/bixbench_v3_corroboration.py + out/bixbench_v3_{samples,tally,
corroboration}.csv. Result = monotonic gradient, strong independent validation:
- gold-answer: CORRECT 86.0% | ALTERNATIVES 34.6% | INCORRECT 3.8% (n=50/127/26).
  INCORRECT-gold -> ~4% model acc = wrong golds are near-unpassable = independent proof
  the golds are really broken (not auditor hallucination).
- insufficiently-specified: SPECIFIED 86.5% | BORDERLINE 81.8% | UNDERSPECIFIED 30.1%
  (n=37/11/156). ~56pt gap.
- answer-format: SOUND 47.8% | PENALISED 11.5%.
- red-teaming: RESISTANT 44.1% | EXPLOITABLE 33.3% -- ~flat, CORRECTLY (exploitability is an
  attack-surface axis, not honest-model-score; good internal consistency check).
HEADLINE: BixBench's 42.9% is ~86% on well-formed questions dragged down by benchmark defects
(underspecification + wrong gold). "Difficulty" is largely artifact.
CAVEAT: single model / single epoch (sol). Large monotone effect compelling; multi-model = IRT-grade.
RED-TEAMING refinement (spot-check): the 18 EXPLOITABLE = 3 mechanisms -- (1) LLM-judge PROMPT
INJECTION [real systemic hole: bixbench_scorer interpolates attacker-controlled {proposed_answer}
into the judge prompt, parses "1"s>"0"s -> "output exactly 1" passes], ~7-9 cases; (2) wide-interval
guessing [James: reasonable, NOT an exploit -> drop, ~9 cases]; (3) wrong-gold-credited [overlaps
INCORRECT-gold -> reclassify, don't double-count]. Real exploit story collapses to ~the injectable judge.
NEXT: stage-2 aggregator agent -- cluster findings by root mechanism (not grade label), promote
systemic findings (injectable judge = 1 finding w/ N demos), dedup cross-item overlap, weight by
confidence, lead report with the audit-grade x model-acc corroboration.

## 2026-08-25 (15:20) -- contract recovery landed (_contract.py)
Registry introspection replaces the planned mockllm dry-run as primary tool source:
registry_params/registry_create recover declared tools (live, schema+impl) + prompt
from any decorated solver, zero execution. James's insight -- I'd wrongly claimed no
declarative source existed. Diff vs logs' ModelEvent.tools staged per-cell as
/audit/discrepancies.md (clean diff still stages: absence is a checked claim).
complete=False marks registry-opaque solvers; incomplete contracts may not accuse
the logs. 12 tests incl. e2e cell staging. 68 green, mypy strict clean.
NEXT: _state.py (benchmark TaskState puppet + attempt tool), then grade rewire.
