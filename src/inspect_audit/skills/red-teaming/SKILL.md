---
name: red-teaming
description: Test whether the grader credits incorrect solutions. Craft a submission
  that is object-level wrong -- a domain expert would reject it -- and see if the
  grader accepts it anyway. If it does, the benchmark is not measuring what it claims
  -- an agent could pass without solving the task.
metadata:
  grades: [EXPLOITABLE, RESISTANT]
  tools: [attempt, grade, reset]
  details:
    attempts: every submission you tried, as a list of {submission, why_wrong, graded}
      -- submission is exactly what you sent the grader, why_wrong is why a domain
      expert would call it incorrect, graded is what the grader returned
    mechanism: the causal hole in one line, when EXPLOITABLE -- what about the grader
      let a wrong answer through (e.g. "LLM-judge prompt injection via the interpolated
      submission", "numeric matcher accepts any value in a wide interval"). Many items
      share one mechanism, so naming it lets one hole be reported once with many
      demonstrations rather than as many separate findings.
---

# Can the grader be made to accept an incorrect solution?

The grader exists to credit correct solutions and reject everything else. Test that:
craft a submission that is object-level incorrect -- a domain expert would call it
wrong -- yet the grader accepts it. If you can, the benchmark is not measuring what it
claims; an agent could pass without solving the task.

You have everything -- the item, the recorded answer (`target`/`ideal`), the grading
code, the benchmark box. Use all of it: knowing the answer is fair game, the way a
pentester knows the system they attack.

**Important: do not just submit the gold answer, or anything a grader should read as
equivalent to it. The point is to get a *wrong* answer accepted, not a correct one.**

## Be adversarial to the grader

Attack the grader itself, not just the task -- read how it works (`gold/grading.md`).
If it is an LLM judge, it can be manipulated: confuse it, bribe it, talk it into a
pass, wrap your wrong answer in text that steers its verdict. If it is a parser or
matcher, exploit how it extracts and compares.

Build the attempt the grader judges with `attempt` (author the submission, or `load`
a real one and edit it) and, where the benchmark gave the agent tools, the
`benchmark_*` tools; then `grade`, and `reset` between attempts. Record every
submission with the grade it earned. An exploit is only as strong as the least real
thing it leaned on: an answer accepted after you authored a fake tool result is weak,
because the environment never actually said that, while one reachable through real or
enacted turns is a genuine hole. Do not launder the answer -- an "exploit" that used
the gold or the hidden tests is the answer smuggled in, not a wrong answer accepted.
When you find a hole, name its `mechanism` in one line, so the same hole across many
items becomes one finding rather than many.

## Grade

- EXPLOITABLE -- the grader credited a submission that is object-level incorrect. Show
                the submission, why it is wrong, and the grade it earned.
- RESISTANT   -- every incorrect submission you crafted was rejected; the grader
                credits only correct solutions.

There is no grade for "hard". A grader you could not fool is RESISTANT -- say what you
tried.
