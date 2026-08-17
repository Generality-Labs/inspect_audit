---
name: gold-answer
description: Judge whether a benchmark item's recorded answer is the answer to
  the question it asks, and whether it is the only one.
---

# Is the recorded answer the answer to this question?

`sample.json` is the item — `target` is the recorded answer, `metadata` may hold
more of the gold. The logs hold every recorded attempt at it, including what each
model answered and how it was graded.

The gold is the claim under audit, not the reference. Establish what the answer
is from sources, then compare.

Grade one of:

- `CORRECT` — it is the answer, and you tried to break it and failed
- `INCORRECT` — the evidence contradicts it
- `ALTERNATIVES` — several answers are independently defensible
- `UNVERIFIABLE` — the evidence cannot settle it

Whatever the item itself cites is the weakest evidence available: the question
was probably written from it, so agreement is circular. A gold confirmed only by
the item's own source is unverified.

If you cannot settle it, grade `UNVERIFIABLE` rather than guessing.

Give the grade, a source for each defensible answer, and what you tried that
failed. Then say what you actually think, including anything you weren't asked
about.
