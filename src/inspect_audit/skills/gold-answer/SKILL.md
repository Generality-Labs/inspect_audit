---
name: gold-answer
description: Judge whether a benchmark item's recorded answer is the answer to
  the question it asks, and whether it is the only one.
---

# Is the recorded answer the answer to this question?

`sample.json` is the item — `target` is the recorded answer, `metadata` may hold
more of the gold. The logs hold every recorded attempt at it.

The gold is the claim under audit, not the reference. Establish what the answer
is from sources, then compare.

## Start with the logs, not the web

Enumerate every distinct answer the field gave and how each was graded. The
sample id is in `sample.json`.

```python
from inspect_ai.analysis import samples_df
df = samples_df("/audit/logs")     # one row per attempt: its answer and its score
```

Then go after the answers marked incorrect. Take each distinct wrong answer and
try to establish it from a source, as though you were arguing for it. Many
capable models converging on something other than the gold is the strongest
evidence available that an item is broken, and the only way to know is to check
what they said. Where an attempt's answer is unclear, read its transcript.

Do not stop at the first source that agrees with the gold. That is the search a
wrong gold survives.

Grade one of:

- `CORRECT` — it is the answer, and you tried to break it and failed
- `INCORRECT` — the evidence contradicts it
- `ALTERNATIVES` — several answers are independently defensible
- `UNVERIFIABLE` — the evidence cannot settle it

Whatever the item itself cites is the weakest evidence available: the question
was probably written from it, so agreement is circular. A gold confirmed only by
the item's own source is unverified.

If you cannot settle it, grade `UNVERIFIABLE` rather than guessing.

Report the grade, and for each defensible answer a **verbatim quote** from the
source that establishes it, with that source. Quote what the source says; do not
summarise what you concluded. If no source you read establishes the recorded
answer, the grade is `UNVERIFIABLE` — however plausible the answer looks. Also
report, for **each** distinct wrong answer the field gave, what you found when you
checked it. Then say what
you actually think, including anything you weren't asked about.
