# inspect_audit

Point it at an Inspect benchmark task and its eval logs; get back a per-item audit of
whether the benchmark measures what it claims.

```python
from inspect_audit import audit_task

task = audit_task("inspect_evals/simpleqa_verified", logs="logs/")
# a normal Inspect Task: run it with `inspect eval`, read it with `inspect view`
```

Each sample of the returned task is an LLM auditor investigating one benchmark item,
inside that benchmark's own environment, with the benchmark's own grader live.

## How it works

**Construction (no model calls).** The benchmark task is resolved from the registry with
the task args the logs recorded. The logs are read once (summaries only, chunked so file
handles stay bounded) and every recorded attempt is grouped by sample id. For each audited
item a staging directory is built, mounted as `/audit/` in the auditor's container:

```
sample.json        the item as the benchmark defines it (answer-bearing metadata redacted)
logs/*.eval        real Inspect logs, sliced to this item's attempts
gold/grading.md    which scorer functions grade this item, and where their code lives
benchmark/*.py     the grader's actual source
env/               the benchmark's own sandbox definition
media/             the item's images, paths rewritten to resolve in the container
discrepancies.md   declared vs recorded tools, diffed mechanically (when logs exist)
```

**Sandbox.** The benchmark's own compose file is merged with an auditor container: their
services run as declared (their default service renamed to `benchmark`; reserved-name
collisions moved aside), our auditor rides alongside as `default` with the benchmark's
package pip-installed and internet on. One compose project per item.

**The auditor** is a react agent. Its skills are the questions, one verdict each:

| skill | question |
|---|---|
| `gold-answer` | is the recorded answer actually correct? |
| `red-teaming` | can the grader be made to accept a wrong answer? |
| `ground-truth-access` | can the answer be reached from inside the environment? |
| `contamination` | was the answer recalled rather than worked out? |
| `answer-format` | does grading punish format rather than substance? |
| `insufficiently-specified` | can a defensible reading reach a different answer? |
| `environment-integrity` | did the environment break attempts? |
| `approach-census` | how did the field actually approach the item? |
| `failure-attribution` | why did failing attempts fail? |
| `other-findings` | anything else, evidenced |

Each skill's frontmatter declares its grades, evidence rules, and tool grants;
`record_verdict` enforces them, and a malformed skill fails at load.

**Tools**, in two namespaces so provenance stays unambiguous. `audit_bash` is a shell in
the auditor's own box; `audit_probe` a shell in the benchmark's box, off the record
(mounted only when a benchmark box exists). `benchmark_<tool>` are the evaluated agent's
own tools, rebuilt from the registry: calling one executes for real in the benchmark box
and records the call into the attempt being built. `attempt` builds the session the grader
will judge — seed it fresh, or load a recorded attempt and append/edit/truncate — with
every message tagged `real` (from the log), `enacted` (actually executed), or `authored`
(written by the auditor). `grade` runs the benchmark's real scorer against that session
and the box, stamped with the provenance mix. `reset` restores the box: soft (git revert +
re-run setup) or hard (recreate the container from its image, re-lay the sample's files,
re-run setup — recovers a deliberately bricked box).

**Concordance** runs before verdicts count: the recorded attempts are re-graded through
this same channel (via inspect's own `score_async` replay path, per source log), and the
fresh grades must match the logged ones. A channel that checked nothing reports
`unvalidated`; a stable disagreement on a deterministic scorer blocks grade-dependent
verdicts. Package and task-arg drift against the log header is reported alongside.

**Output** is a normal Inspect eval log: one score column per skill per item, each with
its grade distribution as metrics. The log viewer is the report.

## Reliability

Measured on SimpleQA Verified (105 items, test-retest): **10.2% flip rate**, but
structured — hard verdicts contradicted ~1% of the time; soft verdicts (`UNVERIFIABLE`)
account for nearly all churn, and flips are search-depth variance rather than reasoning
failures. Policy: hard verdicts stand, softs re-run, contradictions escalate.

## Development

```bash
make install      # uv venv + editable install
make check        # ruff + strict mypy
make test         # pure tests, no docker needed
make test-docker  # the container e2e suite (phoenix, seeding, mirrored tools)
```

Reference material lives in `reference/` (a full local copy of the inspect docs, the
Mohl et al. 2026 transcript-analysis paper and its labelled dataset) and `AUDIT_CATALOGUE.md`
maps the audit items against the ABC checklist. `dev/` holds run artifacts from audits of
public benchmarks.

## Constraints, honestly

Docker only for the hard reset (a k8s/Helm emission exists via `audit_values`, without
reset). Multi-service benchmarks run whole: every box is probed by name, reverted on
soft reset, and rebuilt + re-seeded on hard reset; the mirrored `benchmark_*` tools act
as the evaluated agent did, against its own default box. Programmatic `ComposeConfig`
sandboxes are not yet reproduced. The benchmark's package must pip-install into a python 3.12 image. Logs
are optional (skills that read the field simply skip), and `confidential=True` is a
prompt-level boundary on what leaves the container, not a network one.
