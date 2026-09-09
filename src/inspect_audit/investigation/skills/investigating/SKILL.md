---
name: investigating
description: How to audit a benchmark. Start from the claim people make about it, work out what would have to be true for that claim to hold, turn each assumption into a hypothesis, and test the hypotheses against the source and the recorded runs. Read this before anything else.
---

## What you are doing

A benchmark makes a claim: this score measures that capability. People who cite it
carry a mental model of what the score means, and they defend conclusions with it.
Your job is to find out whether the machinery supports that model, and to say, with
evidence, where it does and where it does not. A benchmark that survives scrutiny is a
result. You are not rewarded for the number of problems you find; you are judged on
whether what you say is true, quantified, and reproducible from your evidence.

Decompose every question three ways: construction (what the items and environment
are), grading (how a response becomes a score), interpretation (what people conclude
from the score). Most real problems are a gap between two of these.

## Start from the claim, not the code

Before reading a line of implementation, establish what the benchmark is claimed to
measure and by whom. Read the paper in full, its abstract and its results tables. Read
the README. Find how the benchmark is actually used: who reports it, in what setting,
described in what words. Fetch what you need with curl. Write one paragraph in your
own words: the claim, the mental model a reader forms, and the conclusions people draw.

Then write down what would have to be true of the pipeline for that model to hold.
Each such assumption is a hypothesis. Keep the list; it is the spine of the audit, and
the report answers it. Add hypotheses as you learn, retire them with evidence.

## Then verify the chain before believing anything

Establish that the paper, the code, the logs and any leaderboard describe the same
thing. Read grader identity, grading template, generation config and metric definition
out of the logs, not out of the documentation; judges and configurations get swapped
silently. Reproduce the headline number from the logs exactly before you perturb or
interpret anything. If you cannot reproduce it, that is your first finding.

## Then the recorded runs

Population first. For every model and configuration, compute everything the logs let
you compute: every outcome category the grader produces, every terminal state, every
error class, every resource limit hit, attempted and completed counts. Put it in one
table with explicit denominators, then sort that table by every column. The rows that
stand out are your leads. Patterns that cluster by model family or provider are leads.
Do this before you read a single transcript, and before you write a word of prose.

Transcripts second, and a lot of them. Sample across models, across outcome classes and
across score range, and read enough that a pattern is a pattern: nothing below roughly
fifteen to twenty cases is a rate. Record what you see per transcript in a table you keep.
Hypotheses come from transcripts as much as from code; read some yourself rather than
only counting.

Targeted checks third. For each live hypothesis, the cheapest observation that would
refute it. Prefer what is already in the logs; then deterministic recomputation; then
anything you can verify independently with the tools you have. Ask of every candidate
defect: what does it predict, and where would I see that prediction fail?

## Rules of evidence

A finding is a lead until you have checked it against the primary source: the code for
a claim about behaviour of the code, the raw transcript for a claim about behaviour of a
model, the recorded score for a claim about grading. Summaries, dataframes and your own
extractors are instruments, and instruments break. An absence ("model X never does Y")
is an extractor bug until you have looked at the raw record and seen the field you
think is empty.

Every number carries its scope: what it counts, for whom, over what denominator, and
whether it includes cached, errored or excluded cases. Report attempted and completed
populations separately. Never present a conditional rate as an overall one.

Distrust a result that is too clean. Before you write a striking number, state the
strongest artefact explanation alongside it, look at the raw points, and check the
denominator. Follow the evidence in both directions: if a suspected defect turns out
to be the design working as intended, say so and record what you tested.

Use Inspect's own machinery to read logs and recompute scores. Do not reimplement it.
Read the documentation under /inputs/docs before using an API you have not used in
this session.

## Working discipline

Keep two files. /workspace/journal.md is append-only: what you did, what you saw, what
you corrected, in order. /workspace/report/findings.json is the register of what is
supported now: id, section (task | grader | harness_environment | aggregation_limits |
agent_behaviour | construction), claim, status (hypothesis | supported | qualified |
retracted), origin (historical | experiment | source | audit_limitation), evidence
(path, location, optional quote), reproduce, limitations. Paths are report-relative
(evidence/counts.csv) or /inputs paths. Supported and qualified findings need evidence.
Neither file substitutes for re-reading the primary source when a conclusion is
challenged or after your context has been compacted.

Keep every calculation as a script with its inputs and its output table under
/workspace/report/evidence. Rerunnable, not a heredoc you cannot find again.
Commands longer than a few minutes go in the background with nohup, output to a file,
and you poll; the shell call itself times out at 300 seconds.

Use the budget. An investigation that stops when the template is full has not
investigated. The report is the last part of the work, not most of it; check the
allowance with budget() and plan to spend the bulk of it on reading and checking.

## The report

Read the writing skill before drafting. The structure is fixed: a three-paragraph
summary, then The task, The grader, The harness and environment, Aggregation and
limits, How agents approach it, How it is built. Each section answers its questions
with evidence; say "not assessed" with a reason where you could not check. Findings
appear under their section. Render with render_report and read it back, look at every
figure with view_image, then publish_report. Rendering is not verification.
