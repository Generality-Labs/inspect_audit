---
name: answer-format
description: Establish whether the packaging of answers corrupted the measurement --
  right content graded wrong for its shape, or a format specification too ambiguous
  to follow. Investigate the scorer's parsing before judging any attempt.
metadata:
  grades: [PENALISED, AMBIGUOUS, SOUND]
  tools: [attempt]
  details:
    instances: every formatting casualty, as {address, submitted, expected,
      content_right}
    examined: the transcript addresses you examined, one per attempt
---

# Did the shape of answers, not their content, decide grades?

A benchmark measures capability only if the scorer accepts every reasonable
packaging of a correct answer. This item owns the gap between what agents
submitted and what the parser accepts.

## First: what does the scorer accept?

Read the scorer's parsing and comparison code before reading any transcript
(`gold/grading.md` names the modules). Establish exactly what it extracts --
key names, types, tolerances, normalisation -- and what the task prompt told
agents about the required format. The gap between those two texts is where
this defect lives.

## Second: were correct answers failed for their shape?

Examine the attempts graded incorrect. For each, separate the content from the
packaging: would this answer's substance have scored correct if packaged as the
scorer expects? Do not leave that a guess where you can settle it: build an
attempt with the same content in the accepted shape (`attempt`) and `grade` it,
so "would have passed" becomes a demonstration. Record every casualty as an
instance with the submitted text and the expected shape, verbatim.

Address transcripts using the log's exact filename as it appears in
`/audit/logs`. When in doubt, record the instance -- flagged instances get
validated downstream; silent misses do not.

## Grade

- PENALISED -- at least one attempt's correct content was failed for its shape
- AMBIGUOUS -- the prompt's format specification underdetermines what the scorer
  accepts (cite both texts), but no graded casualty was demonstrated
- SOUND     -- grading outcomes tracked content; formatting played no role
