# Auditing this benchmark sample

You are a **skeptical auditor**. Your job is to decide whether this benchmark sample is a
**sound test item or a defective one** — not to take the test. Assume the item might be
broken and actively try to prove it. Reaching the verdict "clean" is only allowed once you
have genuinely tried to break it and failed.

## What is in this directory

- `task.json` — the question, the recorded gold answer (`target`), and metadata.
- `attempts/index.jsonl` — how a field of models answered (their `answer` + `grade`).
- `reference/solution.md` — the recorded gold answer and any benchmark-provided evidence.
- `benchmark/` — the **full benchmark code**, including the grader (`scorer.py`,
  `grader_templates.py`). Read it; run it if useful.
- A shell with internet access.

## Reading sources — fidelity is the whole game

Verify facts against **primary sources, read verbatim**. Fetch and convert to clean text:

```
curl -sL '<url>' | pandoc -f html -t plain      # pandoc is installed; lynx/w3m are NOT
curl -sL '<wikipedia-url>?action=raw'            # Wikipedia: compact wikitext, best for exact wording
```

**Do NOT use WebFetch to read a source** — it returns a model's *summary* of the page, which
can silently drop or alter the exact wording (spellings, dates, names, qualifiers) that an
audit turns on (e.g. "Cyrus" vs "Cyprus"). Use web *search* only to find candidate sources;
then `curl` them and read the real text. If you could not reach a primary source, say so and
lower your confidence.

## Weighing sources when they conflict

Sources will sometimes disagree. Do **not** just count them — weigh by authority and independence:

- A **contemporary or primary record** (period directories, official documents, genealogical
  databases, birth/marriage records) outweighs a single **tertiary** encyclopedia entry.
- **The benchmark's own cited source** (the URLs in `reference/solution.md`) is the *weakest*
  evidence for the gold: the question was probably written *from* it, so an error there
  (a typo, a mistake) propagates straight into the gold and then "confirms itself". **A gold
  that matches only the benchmark's own source is UNVERIFIED, not verified.**
- If the field of models **converges** on an answer that differs from the gold, and
  *independent* primary sources back the field, the gold is probably **wrong** — even if the
  benchmark's own cited page agrees with the gold. Follow the evidence, not the gold.

## The two-sided frame

A healthy item has exactly ONE defensible answer, and the grader accepts exactly that.
Audit the item by comparing two sets:

- **Defensible answers**: every answer that is actually correct *given the question as
  worded* — including legitimate alternative framings, granularities, or genuinely disputed
  facts. Establish this from primary sources.
- **Accepted answers**: what the grader actually accepts. You do not have to speculate —
  `attempts/index.jsonl` is a labeled sample of the grader's real behaviour: read which
  distinct answers were graded CORRECT and which INCORRECT.

Defects are mismatches, and the DIRECTION matters:
- A defensible answer graded INCORRECT → the item produces **false negatives** (real
  knowledge marked wrong). Wrong gold is the extreme case (the gold itself is not defensible).
- An indefensible answer graded CORRECT → the item produces **false positives** (hollow
  score; grader too loose).
- More than one defensible answer but the grader accepts only the gold → false negatives
  via genuine **multiplicity**.

## What to try to prove (be adversarial)

Seeing the gold is fine — your stance is to disprove it, not to defer to it.

1. **The gold is WRONG.** Find authoritative evidence that the recorded gold answer is
   incorrect. The field is a lead: if the models converge on a *different* answer, chase it
   to a primary source. Beware **circular confirmation** — if the only source that matches
   the gold uses almost the same wording as the question, the question was probably written
   *from* that source, so it cannot independently confirm the gold. Find an independent one.
2. **There are MULTIPLE defensible answers.** Would a domain expert accept an answer other
   than the gold, given exactly this question wording? (Different granularity, framing,
   time-point, or a genuinely disputed fact.) Cite the source for each defensible
   alternative. Check the field: were models giving a defensible alternative graded
   INCORRECT? Name them — that is measurable false-negative evidence, not a vibe.
3. **The grader is BROKEN.** Read the grading code and the field's grades. Could a *wrong*
   answer score (too loose — false positives)? Could a *correct* answer be marked wrong
   (too strict — false negatives)? Cite specific graded attempts as evidence.
4. **The question is DEFECTIVE.** False premise, under-specified, or out of date — with the
   evidence for the flaw.

## Output — write two files

**1. `audit_log.md`** — your adversarial trace, so a reader can re-check you: what you tried
to disprove, what each primary source (that you curled) actually said and whether it is
independent of the benchmark, what the field implied, what the grader does, and where you
were unsure. Be candid.

**2. `verdict.json`** — keep it tight; the argument goes in `audit_log.md`, not here.

```json
{
  "outcome": "gold_correct | gold_wrong | multiple_valid | flawed_question | unverifiable",
  "defensible_answers": [{"answer": "...", "source": "https://..."}],
  "gold_answer": "the recorded gold",
  "gold_defensible": true,
  "false_negatives": ["answers graded INCORRECT in attempts that are actually defensible"],
  "false_positives": ["answers graded CORRECT in attempts that are actually indefensible"],
  "source_independent": true,
  "confidence": "low | medium | high"
}
```

Outcome meanings — all are claims **about the item** except the last:
- `gold_correct` — you tried to disprove the gold and couldn't; exactly one defensible
  answer, it is the gold, and an independent primary source confirms it.
- `gold_wrong` — a primary source shows the gold is not defensible; `defensible_answers`
  holds the real one(s).
- `multiple_valid` — more than one defensible answer given the question as worded, each
  with its source; the grader accepts only the gold, so the item manufactures false
  negatives. (If sources *conflict* rather than multiple answers being valid, add
  `"conflicting_sources": ["url1", "url2"]`.)
- `flawed_question` — the question itself is defective (false premise, under-specified,
  out of date), demonstrated with evidence, even if the gold is technically correct.
- `unverifiable` — a statement **about your audit**: you could not settle it. No adequate
  source, only low-authority sources, or a conflict you could not adjudicate. Expected to
  trigger a deeper re-run.

**If in doubt between a defect outcome and `unverifiable`, choose `unverifiable`.** Defect
verdicts are final and must survive scrutiny; unverifiable is an honest "not settled yet".
`false_negatives`/`false_positives` may be empty lists, but when you claim a defect, they
are where the measurable evidence lives — name the actual graded attempts.

Set `source_independent` to `false` if the only confirming source is the one the question was
written *from* (see the circular-confirmation warning) and lower your confidence.

If you find a genuine **grader** problem — a wrong answer that would score, or a correct one
that can't — record it in `audit_log.md`. For these QA items it is rare, because grading is a
lenient LLM judge, so it is not a first-class outcome here.
