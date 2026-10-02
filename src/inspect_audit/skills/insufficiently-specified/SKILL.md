---
name: insufficiently-specified
description: Establish whether the question pins down a unique answer. When a question
  leaves choices open that change the result -- method, parameters, data, tie-breaks --
  a correct analysis can land on a value the key does not accept, and the benchmark
  grades the guess of a convention, not the work.
metadata:
  grades: [UNDERSPECIFIED, BORDERLINE, SPECIFIED, UNVERIFIABLE]
  unevidenced: [UNVERIFIABLE]
  details:
    freedoms: the choices the question leaves open that change the answer, each as
      {choice, answer_it_yields} -- a decision and the value a defensible analysis
      reaches by making it
    settles_to_gold: what the question would have to fix for the gold answer to be
      the determined one
---

# Does the question determine a single answer?

A benchmark may intentionally allow several valid solutions. Multiplicity is not
itself a defect if the stated task and acceptance rule admit those solutions. Audit
whether an unstated choice changes which answer is accepted: a competent worker
following the question as written should not need to guess the author's convention.

## First: what is actually being asked?

Read the question and the recorded gold (`sample.json`). State precisely what it asks
for and what answer it treats as correct. Read `gold/grading.md` to see how narrowly
the answer is compared -- a tight scorer over a loose question is where this defect
bites hardest.

## Second: find the open choices

Establish the work the question requires, then find every decision left to the worker
that changes the result: the method or tool, a parameter or threshold, which data or
reference set, a tie-break or rounding convention, an ambiguous referent. For each,
ask what a defensible analyst could choose and what answer that choice yields. A
choice that does not move the answer does not count -- only degrees of freedom that
reach a different value.

## Evidence test: exhibit the ambiguity

Give at least two concrete answers, the reasonable interpretation yielding each,
and evidence supporting each answer under that interpretation. Quote the question's
relevant wording and identify exactly what it omits. Test the strongest objection:
does an explicit qualifier or an ordinary dominant convention already settle it?
A hypothetical alternate universe, a strained reading, model disagreement, or a
failed source lookup does not establish underspecification. Aliases for the same
entity are not different substantive answers. A false premise or wrong gold alone
is not evidence of multiple legitimate readings; report it under gold-answer.

If evidence is insufficient to decide between a real ambiguity and an incorrect
candidate, use UNVERIFIABLE and name what remains unknown. Do not turn an inability
to establish an answer into a claim that the question lacks one. Keep observed
rejections separate from guesses about what a scorer would reject; cite the exact
submission and grade, or label the rejection as untested.

## Third: does a correct analysis miss the key?

If a competent worker following the question as written could reach a value the
scorer rejects, the question is underspecified: it is testing whether the agent
guessed the author's convention, not whether it did the work. Say what the question
would have had to fix -- which choice pinned down -- for the gold to be the only
answer.

## Grade

- UNDERSPECIFIED -- a defensible analysis of the question as written reaches an answer
                   the scorer rejects; give the open choices and the answers they yield
- BORDERLINE     -- the question leaves a choice open, but one reading clearly
                   dominates and the others are strained; name the choice and why
- SPECIFIED      -- the wording and acceptance rule determine the required result
                   or intentionally permit all valid solutions
- UNVERIFIABLE   -- the available evidence cannot establish whether the competing
                   readings and answers are defensible; state the missing evidence

There is no grade for "hard question". Difficulty is not underspecification -- a
question can be very hard and still have one determined answer. Grade the
determinacy, not the difficulty.
