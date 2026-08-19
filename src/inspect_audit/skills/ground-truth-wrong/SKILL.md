---
name: ground-truth-wrong
description: Establish whether the benchmark's own recorded answer is correct. A wrong
  gold answer fails the agents who got it right and rewards the ones who matched the
  mistake -- the measurement is inverted, not noisy. Derive the answer yourself before
  trusting the key.
metadata:
  grades: [WRONG, DUBIOUS, SOUND]
  details:
    derivation: how you established the true answer -- the computation, source, or
      reasoning you ran, and what it produced
    should_be: the answer you believe is correct, when it differs from the gold
---

# Is the benchmark's own answer correct?

A benchmark can only measure capability if its answer key is right. This item owns
one failure: the recorded gold answer is itself wrong, so the grade rewards matching
an error rather than solving the task.

## First: what is the question, and what does it claim the answer is?

Read the question and the recorded answer (`sample.json` holds the item; the gold is
its `target`, and `ideal` where present). Establish exactly what is being asked and
what value the benchmark asserts is correct. Read `gold/grading.md` to see how that
answer is compared -- an answer key can be wrong in content even when the scorer
parses it fine.

## Second: derive the answer yourself

Do not take the key on trust. Establish the true answer independently: work the
computation, consult the authoritative source, or reason it through from first
principles. You have a shell with the internet -- use it. Where the recorded
attempts computed the quantity, read their working: a transcript that reaches a
different value by sound method is evidence against the key, not against the agent.
State your derivation and what it produced.

## Third: compare

Set your answer against the gold. They agree -> the key is sound. They disagree and
your derivation holds -> the key is wrong, and every attempt was graded against a
mistake. You cannot settle it -> dubious, and say what you would need to.

## Grade

- WRONG    -- you derived the answer and it contradicts the recorded gold; give the
             derivation and what the answer should be
- DUBIOUS  -- the gold is questionable or you could not verify it, but you cannot
             show it wrong; say what is unresolved
- SOUND    -- you derived the answer independently and it matches the gold

There is no grade for "did not check". If you cannot derive the answer at all, say so
in `remarks` and grade DUBIOUS, not SOUND.
