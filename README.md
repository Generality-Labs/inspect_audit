# inspect_audit

Strap it to an Inspect task and a logs directory; get back a well-shaped report on whether
that benchmark measures what it claims.

```
audit(task, logs) -> report
```

## Pipeline

1. **Pack** — from the task: dataset (id, input, target, metadata), the scorer, the sandbox
   spec, the benchmark source. From the logs: every model's attempt + transcript, joined on
   `sample_id`. Output: one self-contained **audit cell** per unit.
2. **Triage** — cheap cross-sample statistics computed once over the whole matrix: IRT
   misfit (negative discrimination = wrong-gold signature), field convergence, never-solved,
   step-count anomalies. Does double duty: ranks where to spend, and is handed to each agent
   as context.
3. **Audit** — one agent per cell, inside the benchmark's own container (fallback: the
   generic image in `docker/`), armed with `skills/`.
4. **Synthesize** — a top-level agent over all cell reports writes the final document.

## The audit cell

A directory that is also a container payload:

```
task.json          sample id, input, target, metadata
benchmark/         full benchmark code incl. grader + ACTIVE_CONFIG.md
reference/         recorded gold + the benchmark's own cited evidence
attempts/          how the field answered (index.jsonl [+ .eval transcripts])
AUDIT.md           the procedure (from skills/)
verdict.json       OUT: validated schema
audit_log.md       OUT: freeform reasoning, every claim carrying a receipt
```

**Unit of audit.** A cell is per *item* (gold, grader, whole field) or per *attempt* (one
trajectory in context). Item-cells catch label/grader/question defects; attempt-cells catch
transcript-observable defects (ground-truth access, tool failure, guessing, format
ambiguity). Same contract, different packing.

## Output contract

Structured verdict is mechanically validated (`validate_cell.py`). Freeform reasoning is
allowed to vibe, under one rule: **every claim carries a receipt (URL, file:line, named
attempt) or is explicitly marked speculation.**

Defect outcomes are two-sided — compare the set of *defensible* answers against the set the
grader *accepts*:

| mismatch | effect | outcome |
|---|---|---|
| defensible answer graded INCORRECT | false negatives | `gold_wrong`, `multiple_valid` |
| indefensible answer graded CORRECT | false positives | grader error, or **unintended route** (reward hack / guess / leakage) |
| question itself broken | both | `flawed_question` |
| couldn't settle | — | `unverifiable` (triggers re-run) |

## Reliability

Measured on SimpleQA Verified (105 items, test-retest): **10.2% flip rate**, but structured
— hard verdicts contradicted ~1% of the time, soft verdicts (`unverifiable`) churned 7/8.
Flips are search-depth variance, not reasoning failures. Policy: hard verdicts stand, softs
auto-rerun, contradictions escalate to a stronger model.

## Validation

Two independent sources, doing different jobs:

- **Injection** (`calibration/`) — mutate verified-clean cells (name typos, digit swaps,
  unit/coordinate swaps, entity swaps). Labels are *certain*, so this is the y-axis for the
  compute-scaling curve (model tier × samples × token budget × tool affordances).
- **Mohl et al. 2026** (`reference/abc-scout-scanners/`) — human severity labels (0–3) on
  real agentic transcripts for four criteria, with per-rater files. External referent, and a
  head-to-head against published scanner F1s (0.14–0.74). Caveats: labels are
  transcript-level and admittedly noisy; label-error/wrong-gold is *not* among their four
  criteria; we are co-authors, so it is a referent, not an oracle.

## Reference material

- `reference/mohl2026_transcript_analysis.{pdf,txt}` — arXiv 2607.27518
- `reference/abc-scout-scanners/` — the paper's dataset: eval logs, scan results, human
  validation CSVs (`scans/<criterion>/{dev,test}/validation/`), and `synth/` injected runs
- `reference/scanner_evaluation/` — the paper's code

## Status

SimpleQA Verified is the reference implementation: 115 cells packed and audited, IRT triage
run (`out/irt_items.csv`), results aggregated (`out/results.csv`). Next: SWE-bench Verified
via the Mohl transcripts, injection calibration, and the synthesizer.
