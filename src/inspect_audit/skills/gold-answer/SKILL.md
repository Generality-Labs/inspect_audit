---
name: gold-answer
description: Judge whether a benchmark item's recorded answer is the answer to
  the question it asks, and whether it is the only one.
metadata:
  grades: [CORRECT, INCORRECT, ALTERNATIVES, UNVERIFIABLE]
  unevidenced: [UNVERIFIABLE]
  details:
    independent: whether any source you cite is independent of the item's own
      cited sources (true/false)
---

# Is the recorded answer the answer to this question?

`sample.json` is the item. When the item stores an answer, `target` holds it and
`metadata` may hold more of the gold. Some benchmarks store none: correctness is a
predicate the grader computes over the submission — it executes the submitted
program, runs the tests, checks the end state. There an empty `target` is not a missing
or wrong answer. The gold is any submission the grader accepts, so what you audit is
whether a valid solution exists and whether that acceptance predicate is sound —
never whether the empty string is correct. The logs hold every recorded attempt at it.

The gold is the claim under audit, not the reference. Establish what the answer
is from sources, then compare.

The benchmark's own machinery is not such a source. Its grader, its generation
script, its answer key and its metadata all define what the benchmark *expects*;
none of them tells you whether that expectation is right. Reading the code and
concluding "the source says X, so X is correct" is the same self-confirming
circularity one level deeper — the benchmark vouching for itself. Where the answer
is something you could reproduce — a count, a computation, a lookup, a derivation —
reproduce it yourself, independently of the benchmark's own code, and compare that
to the recorded answer. Fix your method and its parameters *before* you look at the
recorded answer, or your reproduction will drift toward it — a result you tuned until
it matched the gold is not independent evidence. Fall back to external sources only
when independent reproduction is genuinely out of reach, and if even those cannot
settle it, grade `UNVERIFIABLE` rather than accept the benchmark's account of itself.

## Defensible alternatives are not merely plausible guesses

For each rejected candidate, separate two questions: does reliable evidence support
its factual content, and does it answer the question under a reasonable reading of
its actual wording? Model confidence, repetition across models, and resemblance to
the gold are not evidence. Do not invent an interpretation solely to rescue a guess.
An explicit date, population, location or definition in the question rules out
answers that fit only a different scope.

Distinguish these findings:
- A supported alternative excluded by the key: ALTERNATIVES, with evidence for the
  gold and the alternative and the wording that permits both.
- A supported candidate contradicting an unsupported/wrong gold: INCORRECT when
  evidence establishes the error; uncertainty alone is UNVERIFIABLE.
- Two spellings or names for the same answer: an equivalence/grading issue, not
  two substantive answers. The gold can remain CORRECT while a rejection is wrong.
- A correct gold and a factually wrong but plausible candidate: CORRECT, not
  ALTERNATIVES. Explain the decisive constraint or contrary evidence.

Record the candidate's exact text, original grade and log/sample/turn reference,
source quote, and whether the rejection is justified, unjustified, or unresolved
in the verdict details. Include a reason for each reviewed candidate. Do not infer
that every unreviewed candidate is incorrect. Preserve contradictions and additional
claims in the actual submission; neither silently trim them away nor assume the
judge's accusation of a contradiction is true. Source-check the disputed claim.

Read intermediate attempts too. In binary-feedback logs these are stored in
sample.metadata.feedback_attempts: each entry has attempt, answer, feedback and
grade. The final sample output/score does not describe every earlier rejection.

## Start with the logs, not the web

Enumerate every distinct answer the field gave and how each was graded. The
sample id is in `sample.json`.

```python
from inspect_ai.analysis import samples_df
df = samples_df("/audit/logs")     # summary rows; inspect available columns
```

Enumerate all epochs and read the actual submissions, not just the scorer's
extracted answers. Reuse the saved attempt review table across skills. Account
for unscored and unresolved cases; compute counts in code. `samples_df` defaults
do not include full completions: inspect its columns and use Inspect's sample
reading API for the submitted content.

Read sources verbatim with curl: never judge a source through anything that
summarises it, because a summary normalises the exact detail that is usually the
finding. A search engine is for finding candidate sources, never for establishing
what one says.

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
- `ALTERNATIVES` — several answers are independently defensible *and the grader
  credits only one*, so a correct answer is marked wrong. Where the grader instead
  credits every answer that meets its criterion (a functional grader that accepts
  any valid solution), multiplicity is the design, not a fault — grade `CORRECT`.
- `UNVERIFIABLE` — the evidence cannot settle it

Weigh a source by what it is, not by who cited it. A **primary record for the
fact** — an organisation's own account of itself, an official register, a
contemporary document — is strong evidence even when the item cites it. A
**derivative** source — an encyclopedia, an aggregator, a syndicated biography —
is weaker, and weakest of all when the item cites it, because an error there
propagates into the gold and then confirms itself.

Grade `UNVERIFIABLE` only when you could not establish the answer at all. Good
evidence that happens to be the source the item cites is still evidence; record
how independent it was in the `independent` field instead of downgrading the
grade.

Report the grade, and for each defensible answer a **verbatim quote** from the
source that establishes it, with that source. Quote what the source says; do not
summarise what you concluded. If no source you read establishes the recorded
answer, the grade is `UNVERIFIABLE` — however plausible the answer looks. Also
report, for **each** distinct wrong answer the field gave, what you found when you
checked it. Then say what
you actually think, including anything you weren't asked about.
