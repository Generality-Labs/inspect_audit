# Inspect Auditor — the catalogue (working)

Design note, not code. Combines the ABC checklist (arXiv 2507.02825, read from the
primary source) with the flaw/realisation framing we worked out. This is the
starting switchboard; expand it.

## The core

A benchmark makes two **promises**. A flaw is a broken promise; an audit hunts breaks.

1. **Measurement** — "I measure capability C." So C ≡ task-success S ≡ score R.
   Breaks split two ways:
   - *Task validity* (C ≡ S): can you succeed without the capability (**shortcut**,
     false positive) or fail despite it (**obstacle/impossible**, false negative)?
   - *Outcome validity* (S ≡ R): does the score track actual success — grader too
     broad (credits wrong), too narrow (rejects right), or wrong reference?
2. **Containment** — "the agent is sandboxed." Breaks: the box can be crashed,
   escaped, or reach what it shouldn't. (ABC only gestures at this; it's ours.)

Two things anchor the whole thing:

- **The construct is a declared input, not an inference.** You cannot audit
  "C ≡ S" if nobody stated C. The configurer / synth declares what the benchmark
  measures and how its grader decides success; the switchboard hangs off that. A
  benchmark with no stated construct is reported *unassessable* (ABC R.5-6), not
  guessed at. This is the fix for the misinterpretation problem: the auditor stops
  inventing its own C.
- **Every check is one binary/trinary question** answered on a single scale:
  - `observed` — the flaw corrupted a recorded reading (evidence: the instance)
  - `latent`  — the flaw is present but didn't bite this run (evidence: a
    construction/reproduction)
  - `absent`  — searched, demonstrably not there (an active claim, not a default)
  - `unclear` — couldn't establish the reference
  - plus an `by-design` tag: present but a documented, endorsed choice → not a flaw.

  `observed` vs `latent` is our extension over ABC: ABC is build-time (no runs), so
  it can only ever ask the `latent` question. The audit adds "did it actually bite".

Routing: ABC organises outcome-validity by **grader modality** (string-match /
substring / LLM-judge / unit-test / fuzz / e2e / state-modification / answer-match).
That is the routing key — inspect the scorer type, fire only the applicable subset.

Infrastructure (not questions; shared preamble the questions read): declare/recover
the construct, read the grader, map the field (approach-census), the concordance
gate (prove the reconstruction before accusing), failure-attribution (the honest
counterweight: "benchmark's fine, the model can't").

Permanent **open residue**: "what break did you find that no question covers." The
board is a floor, never a ceiling — the pilot's best finds came from here.

---

## The kernel — ABC items as binaries, with verified examples

The obvious, clearly-huntable checks, one binary per ABC item, each carrying a
worked example read from the ABC paper's own assessment (§5.2, verified against the
raw text — not the summariser). Every binary answers observed / latent / absent /
unclear. The example is the calibration case: "this is what a real one looks like."

**[T.7] Wrong gold** — find a sample whose recorded answer is not correct.
- Counts: a recorded gold contradicted by an independent reproduction or a primary source.
- Not: an answer you merely dislike; you must establish the truth independently.

**[T.5] Ground truth not isolated** — find a way to reach the answer from the environment.
- Counts: SWE-Lancer — the test files sit in a password-protected ZIP whose contents
  are still listable and overwritable; an agent replaces them with `assert 1 == 1`
  and scores 100% without solving anything.
- Not: task input that legitimately contains the information the task is about.

**[T.4] Stale state leak** — find state from a prior task/sample that leaks in.
- Counts: KernelBench — ground-truth answers left in GPU memory, reachable by an
  out-of-bounds read.
- Not: the sample's own intended setup state.

**[T.8] Task not solvable** — find a task that can't be solved, so the score is meaningless.
- Counts: τ-bench — 38% of the airline subset (6% retail) are intentionally
  unsolvable, and since success is defined as *leaving the environment unchanged*,
  an agent that does nothing passes them.
- Not: a task that is merely hard.

**[T.6] Environment not frozen** — find an outcome changed by external drift since release.
- Counts: OSWorld — 13/46 chrome tasks broke because live websites changed layout/URLs
  under the HTML-selector graders, underestimating UI-TARS by 28% absolute.
- Not: a genuine capability failure inside a stable environment.

**[O.b.2] Success by listing/dumping** — find a submission credited for listing everything, not answering.
- Counts: τ-bench — verbatim database text is the ground truth graded by substring,
  so an agent that dumps the entire database passes (≈40% overestimate).
- Not: a concise answer that happens to contain the target.

**[O.b.3 / O.h.2 / O.g.3] Success by guessing** — find an answer reachable by guessing / trivial change.
- Counts: τ-bench — trivial states or substrings as ground truth, so a random change
  lands correct.
- Not: a small but genuinely reasoned answer space.

**[O.c.1] Judge unvalidated** — find the LLM-judge accepting a wrong answer or flipping on reruns.
- Counts: WebArena — for "N/A" tasks the LLM judge accepts an empty reply as correct,
  so a trivial agent passes.
- Not: a judge disagreeing with you where it is actually right.

**[O.e.1 / O.e.2] Fuzz coverage incomplete** — find inputs under which a credited solution fails.
- Counts: KernelBench — the fuzzer varies only tensor values, never shapes or memory
  layouts, so kernels that fail under other configurations still pass (≈31% overestimate).
- Not: inputs outside the task's declared domain.

**[O.a.1 / O.a.2] Grader too narrow** — find a correct answer rejected for phrasing/format.
- Counts: a semantically equivalent expression, or one with redundant words, marked wrong.
- Not: a genuine format requirement the prompt stated and the answer ignored.

**[R.13] Trivial agent scores** — find that a no-op or trivial agent beats chance.
- Counts: τ-bench no-op passing unsolvable tasks; WebArena empty reply passing "N/A".
- Not: a legitimate strong baseline.

**Containment kernel (ours, not ABC — ABC only gestures at egress via CVE-Bench):**
- **Box crashable** — find a way to crash/deny the environment (needs cgroup limits +
  throwaway box before detonating). **Egress** — find the box reaching network it
  shouldn't (CVE-Bench ungated outbound servers). **Escape** — find a break out to
  host/siblings.

---

## The switchboard (fuller consolidated cross-map)

Each row: our question (the binary X) · which ABC items it subsumes · notes.
★ = starter set (covers what we've actually seen bite). ABC examples kept because
they make the X concrete.

### Measurement / Outcome validity  (S ≡ R — does the score track success)

| # | Question (find a case where…) | ABC | Notes |
|---|---|---|---|
| ★ OV1 | the recorded gold answer is wrong | T.7 | mislabelled golds, stale answers |
| ★ OV2 | a correct submission is rejected (grader too narrow) | O.a.1, O.a.2, O.b.1, O.h.1 | semantic equivalents, redundant words, negation, format assumptions |
| ★ OV3 | a wrong submission is credited (grader too broad) | O.d.1, O.b.2, O.g.1, O.g.2 | SWE-bench weak tests, "list all answers", full-DB-dump, state under-check |
| OV4 | the tests are too weak to catch a plausible wrong solution (adequacy) | O.d.2, O.e.1, O.e.2, O.e.3, O.f.1, O.g.3 | coverage/complexity; fuzz input coverage; branch coverage; state complexity |
| OV5 | the LLM-judge is inaccurate or self-inconsistent | O.c.1 | pilot accuracy + stability of the judge |
| OV6 | grading is non-deterministic (unrepeatable scores) | O.f.2 | e2e/environment flakiness in the grader |
| OV7 | a quality-metric can be hacked without doing the task | O.i.1 | metric ⟂ reasoning process |

### Measurement / Task validity  (C ≡ S — success iff capability)

Shortcuts (succeed without the capability → false positive):
| # | Question | ABC | Notes |
|---|---|---|---|
| ★ TV1 | the answer is reachable from the environment (oracle) | T.5, T.4 | ground-truth-access; KernelBench GPU-memory leak |
| TV2 | a route to credit bypasses the task (edit tests / patch scorer) | (SWE-Lancer) | work-bypass |
| ★ TV3 | the answer can be guessed / is unearned | O.b.3, O.h.2, O.g.3 | small answer space, format tell |
| TV4 | stale state from a prior task/sample/epoch leaks in | T.4 | box not fresh per sample |
| ★ TV5 | the answer is reachable from training memory (contamination) | R.3 | reported as reporting; auditable as a shortcut |

Obstacles (fail despite the capability → false negative):
| # | Question | ABC | Notes |
|---|---|---|---|
| TV6 | the task is not actually solvable | T.8 | τ-bench impossible tasks; empty response passes |
| ★ TV7 | a tool/dependency/infra failure blocked the intended path | T.1, T.2, T.3 | env-integrity; versions, availability, API interruptions |
| TV8 | external drift changed outcomes (env not frozen) | T.6 | OSWorld selectors; live-website reliance |
| TV9 | the question underdetermines the answer (underspecified) | (O.h.1-adj) | defensible answer the grader rejects |
| TV10 | grading enforces a requirement the prompt never states | — | hidden requirement |
| TV11 | a limit cut off capable work mid-progress | — | turn/token/time budget too tight |
| TV12 | a refusal/non-attempt was scored as a capability failure | — | won't ≠ can't; the elicitation-adjacent gap |

### Containment  (the sandbox holds — our extension)

| # | Question | ABC | Notes |
|---|---|---|---|
| C1 | the environment can be crashed / denied | — | brick-the-box (needs cgroup limits + throwaway box first) |
| C2 | the box reaches network it shouldn't | (CVE-Bench) | egress; "ungated outbound servers" |
| C3 | the container can be escaped (host/siblings) | — | sandbox breakout |
| C4 | a submission causes out-of-scope side effects | O.g.2 | relevant + irrelevant states |

### Reporting  (mostly meta / partially auditable)

| # | Question | ABC | Notes |
|---|---|---|---|
| R-anchor | is the construct declared and construct-valid | R.5, R.6 | THE anchor; unassessable if absent |
| ★ RP1 | contamination measures present | R.3, R.4 | overlaps TV5 |
| RP2 | a trivial/no-op agent beats chance | R.13, R.12 | cheap, high-signal |
| RP3 | claims are statistically underpowered | R.10, R.11 | report-level |
| RP-meta | dataset + harness open; limitations documented | R.1, R.2, R.7, R.8, R.9 | author-process, not a flaw-hunt |

Build-only ABC items with no audit-question (author recommendations, kept for
completeness): T.9 (ship an oracle solver), T.10 (inspect pilot outliers — a method,
folds into approach-census), R.11 (interpretation guidelines).

---

## Where ABC and us differ (the expansion axes)

- **Manifestation.** ABC has no observed/latent — it is pre-run. Our whole
  `observed` column is new signal ABC structurally cannot produce.
- **Containment family.** ABC has no crash/escape items and only an egress
  *example* (CVE-Bench). C1-C3 are ours.
- **Obstacle gaps ABC gave us.** TV6 (unsolvable), TV8 (drift), TV4 (stale state)
  were missing from our catalogue; ABC surfaced them.
- **Mutual gap.** Elicitation quality (was the scaffold fair) is absent from *both*
  ABC and us. TV12 is the closest we have; worth a dedicated question.

## Counts (verified against the primary source)
- ABC: 42 checklist items (T:10, O:19, R:13); ~29 map to audit questions, ~13 are
  author-recommendation / reporting-process.
- This catalogue: ~27 questions across the two promises + reporting, 8 in the
  starter set (★).
