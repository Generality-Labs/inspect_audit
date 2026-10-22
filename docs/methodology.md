# Methodology, template and records

About the investigator agent: what it is asked to check, how its report is laid out, and the records it should leave for the findings store.

Written 2026-10-12 after Justin's Auditor architecture sketch (Slack, 2026-10-09) and a read of Epoch AI's benchmark reviews. It settles one question: whether the investigator keeps being handed the report template to tell it what to look for, or produces records that any template can render.

## Today

The report template does two jobs at once. Its section comments are a checklist of what to look for, and the `section` enum in `findings.schema.json` is the list of report headings, so a finding is filed under a heading rather than under a check. The layout and the method are one file. James's reading is right: that is why the template "governs what the investigator looks for".

## The split

Three things, each with one owner:

- **A methodology** says what to check and how to judge it. It is a versioned document, like a taxonomy: the checks with ids and the question each asks, guidance for answering, the scale and any threshold per check, the sampling policy, the evidence a check needs, and the verdict rule if the methodology has one. `inspect-audit@2`, Epoch's rubric and the eval-validity-review's four dimensions are three methodologies the same investigator can run. A check an organisation wants that nobody else has ("is the author reputable?") is one more entry.
- **Records** are what the investigator produces: a Run, a Profile, Findings, Assessments and Coverage, in the shared schema, citing the methodology's check ids. This is the `.audit` bundle in Justin's sketch and what the store holds.
- **A template** renders records into a document: which records to show, in what order, as which tables and figures. A template may also declare prose blocks, each with a brief for what it should say and which records it draws on. Epoch's page wants an interpretation and a summary; our Quarto report wants a summary, an architecture narrative and a discussion per section; the table site wants none. Those blocks are synthesis, written when the template is rendered by the synthesis agent from the records, not by the investigator during the investigation. Re-rendering with a new template never re-runs the audit.

The investigator's brief becomes the methodology plus the subject seed, operator steer, budget and exposure policy, and the list of templates to render afterwards. That is the "audit configuration".

## What Epoch's pages need from the records

Three Epoch reviews (HLE, SimpleQA Verified, SWE-bench Verified) and the methodology page have one anatomy: header with verdict and snapshot; interpretation and composition counts; a question audit with sampling design, method, a defect table and the rate against a threshold; elicitation and grading setup; limitations; the rubric as rows with a status and a note; an author-response slot. Mapped onto the records:

| Page element                                                                           | Record                                                                                                       | Gap today                                                                      |
| -------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------ | ------------------------------------------------------------------------------ |
| Verdict, rubric rows (reviewability, four minimum checks, seven quality questions)     | Assessment per row: criterion id in the methodology, scale, level, reason, evidence, linked findings, author | A measured quantity: numerator, denominator, threshold                         |
| Defect table, benchmark-level flaws, cited external audits                             | Finding with a sample location; `historical` origin for someone else's audit                                 | None                                                                           |
| Question audit: sample of N, stratified, unit states, hand confirmation of agent flags | Coverage                                                                                                     | The sampling design: population, sample size, strategy, strata, expansion rule |
| Interpretation, composition counts, elicitation and grading setup, snapshot            | A Profile of the subject                                                                                     | No such record; the orient stage writes it as a brief                          |
| Reviewers, agents, dates, cost, "partial review, stopped after section 2"              | Run with outcomes per check                                                                                  | None                                                                           |
| Author response, citation, licence                                                     | Template-level metadata                                                                                      | None                                                                           |

The three gaps are additions within schema 0.x: optional fields and one new record type. They are described in [finding-schema-envelope.md](finding-schema-envelope.md) under "Records beside Finding".

## The methodology format

A YAML or JSON document with `id`, `version`, `status`, `source`, a `verdict` rule where one exists, a `sampling` policy, and `checks`. Each check has `id`, `section`, `name`, `question`, `guidance`, `scale` (a named list of levels), optional `threshold` (a measure name, a comparison and a value), `evidence` (what a supported assessment must cite), and optional `taxonomy` cross-references into `inspect-audit@2` so one investigation can serve two methodologies. [methodologies/epoch-rubric-1.yaml](methodologies/epoch-rubric-1.yaml) is the first instance, written from Epoch's public documentation. Where the chess and SciCode bundles already carry assessments against `inspect-audit@1`, those checks are the methodology; `framework/checks.json` in a bundle is exactly this format without the guidance and scales.

## What changes in the investigator

- `findings.schema.json` loses the `section` enum of report headings and takes `check` ids from the methodology in the brief.
- The report template's section comments move into the methodology's `guidance`; the template keeps only layout.
- `assessments.json` gains `measure` (numerator, denominator, threshold) on checks whose methodology defines one; `coverage.json` gains `sampling`.
- A Profile record is written at the end of the orient stage.
- The synthesis agent takes a template and the records and writes whatever prose blocks that template declares.

None of this is on the roadmap's critical path for the table site. It is the shape the investigator and the standard should converge on, and the next investigation run is the place to try the Epoch methodology against a real subject to find what it cannot fill: reviewability needs disclosure facts the seed does not carry, and the quality questions need multi-run statistics.
