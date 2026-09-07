---
name: red-teaming
description: Test whether the grader credits incorrect solutions. Craft a submission
  that is object-level wrong -- a domain expert would reject it -- and see if the
  grader accepts it anyway. If it does, the benchmark is not measuring what it claims
  -- an agent could pass without solving the task.
metadata:
  grades: [MAJOR_EXPLOIT, MINOR_EXPLOIT, RESISTANT]
  tools: [attempt, grade, reset]
  details:
    attempts: every submission you tried, as a list of {submission, why_wrong, graded}
      -- submission is exactly what you sent the grader, why_wrong is why a domain
      expert would call it incorrect, graded is what the grader returned
    mechanism: the causal hole in one line, for an exploit -- what about the grader
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
An answer the grader was built to accept -- a documented tolerance, an accepted alias, a
normalization -- is the grader working as intended, not a gap; if the design itself looks
too lax, that is a note for `other-findings`.

The test for a real exploit is the **wrongest submission the hole admits**. When you find
something the grader overlooks or fails to check, do not argue about whether it "should"
have been checked -- push it. Abuse everything the grader ignores, keep satisfying
everything it still checks, and see how wrong an answer you can still get credited. What
you can reach is what the hole is worth:

- If the worst you can push through is still a **correct** answer -- the grader was merely
  loose on something that does not bear on whether the answer is right -- it is **not an
  exploit**, however plainly it violates a stated requirement. Note the looseness in
  `other-findings`; it does not go here.
- If you can get a **genuinely wrong** answer credited -- one a domain expert would reject
  on the merits -- it is an exploit. Grade it by reach:

  - **MAJOR_EXPLOIT** -- a non-solution passes, or the hole is realised in the recorded
    attempts, or it would move the benchmark's reported numbers; the grader is failing to
    measure the capability.
  - **MINOR_EXPLOIT** -- a genuinely wrong answer slips through only narrowly, unrealised
    and changing no score.

Grade by how wrong the admitted answer is and how far it reaches, never by how squarely it
violates a stated rule. If you cannot construct a genuinely wrong answer the grader
accepts, there is no exploit -- say what you tried.

When the grader scores by running the submission -- it executes the program and checks
its output, simulates the actions and checks the end state -- any submission that meets
that criterion is correct by construction. A different solution that meets the criterion
in full is a valid solution, not an exploit, and how it behaves on inputs the grader
never tests is out of scope. An exploit here is a submission the grader credits that does
*not* meet the criterion -- e.g. the executor credits a program whose output is wrong.

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

Every exploit grade means you got a **genuinely wrong** answer credited -- the wrongest
submission the hole admits is one a domain expert would reject on the merits. Give that
submission, why it is wrong, and the grade it earned. If the wrongest thing the hole
admits is still a correct answer, the grader is merely loose on something orthogonal to
correctness: that is RESISTANT here and a note for `other-findings`.

- MAJOR_EXPLOIT -- a non-solution passes, the hole is realised in the recorded attempts,
                  or it would move the reported numbers.
- MINOR_EXPLOIT -- a genuinely wrong answer slips through only narrowly, unrealised and
                  score-neutral.
- RESISTANT     -- every genuinely wrong submission you could construct was rejected; the
                  grader credits only correct answers (note any looseness orthogonal to
                  correctness in `other-findings`).

There is no grade for "hard". A grader you could not fool is RESISTANT -- say what you
tried.
