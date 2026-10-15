# Methodology, template and records

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

## What landed (2026-10-13, branch `laurence/epoch-methodology`)

The `epoch` report format now runs on this split. `inspect_audit.methodology` loads a methodology file (`investigation/methodologies/epoch-rubric-1.yaml`, the same file as [methodologies/epoch-rubric-1.yaml](methodologies/epoch-rubric-1.yaml); a test keeps them identical) and exposes checks, scales, form text, fields, thresholds, sampling and a structured verdict procedure. `inspect_audit.records` holds Profile, Assessment (with `measure`, `impact` and `fields`), Sampling and Prose, each validated against the methodology or the template. `inspect_audit.template` loads `investigation/templates/epoch-review.yaml` (header, prose blocks with briefs, limits and the verdicts they belong to, rubric layouts) and renders the page. `_epoch.py` is the glue: it stages the files and schemas, computes each thresholded check's measure from `coverage.json` (only when `sampling.json` says the sample was selected for prevalence), derives the verdict by the procedure, and renders. Findings in this format are filed by `check` id. `scripts/convert_epoch_review.py` converts a pre-split bundle; the two converted Chess Puzzles investigations are fixtures with golden pages under `tests/fixtures/epoch/`. Prose is still written by the investigator at the end of the run rather than by a separate synthesis agent; the GL format is unchanged.

## Order of work, eval models and sample provenance (2026-10-14)

- The Epoch prompt now walks the rubric in its own order (orient and sample; reviewability; item audits; attempts; minimum standard; quality; publish), each stage ending with a record and a `check_report` run. Attempts come after the items are audited, never before, and only on the sampled ids.
- `investigate` takes `eval_models` beside `worker_models`: workers audit, grade and probe; eval models are the only candidates a benchmark job may run, and `eval_budget_usd` (a third of `budget_usd` when eval models are given) caps what benchmark jobs may hold or spend between them. Without `eval_models` every worker may still be a candidate, as before; an explicit empty list forbids attempts.
- Benchmark jobs on the direct provider are sent with `strict_tools: false`: Inspect's OpenAI-compatible provider otherwise marks tool schemas strict, and strict mode refuses optional parameters.
- `sampling.json` gains `drawn_by`. A sample drawn at random by someone else is recorded as its design with the drawer named; `purposive` is for items chosen for a reason. A thresholded check that passes on purposive counts leaves the review Incomplete; a Flag on a measured impact, or the assessment's own measure, still stands.

## Write as you go (2026-10-14)

A validation run on BFCL followed the rubric order correctly, then spent its last hours confirming candidate labels and repairing two items, and reached its working-time limit with every assessment row still Not Reviewed: records on disk, nothing derivable, nothing published. The prompt and the writing skill now say that the scoring row is written at its provisional level as soon as the item labels exist, that later stages update rows rather than save them, that confirmation and repairs are refinements of a derivable review rather than prerequisites, and that at half the working time the agent stops refining and publishes. `budget()` reports the working time used beside the money. `resume: hawk:<eval set id>` continues a run that ran out of time anyway, from the state it saved.

## The review stops at the first Flag (2026-10-14)

The rubric's rows read "Flag [stop → Flawed]", and Epoch's own reviews leave the rows after a Flag not reviewed. The methodology text said the opposite ("a Flag does not stop it") and the BFCL reviews assessed everything after a scoring Flag. The rule is now the published one: the minimum-standard checks after the first Flag in rubric order, and every quality check, are Not Reviewed; `derive_verdict` refuses a level written there. The prompt and the writing skill say where the observations that would have filled those rows go instead.

## The clock is the item audits (2026-10-15)

Every logless review so far spent under an hour generating and the rest waiting on child jobs, and the child jobs that take hours are the item audits: one agent per item, 30 to 85 minutes each, run on ten connections, so a 48-item batch runs in five waves. The BFCL validation run then audited the whole sample a second time against its attempts, which doubled the longest stage and ran it out of working time. Three changes: an audit job's `max_connections` must be at least its item count, up to the policy cap of 50, or `hawk_submit` refuses it; the attempts job goes out beside the item batch as soon as the sample is fixed and the pilot has passed, since the auditors never see attempts, and is read once the labels exist; and impact is measured by re-running gold-answer on the defective items alone with the attempts job as the logs argument. The prompt also tells the lead that the auditors read item media and that it opens an item's image only to adjudicate a dispute: an HLE run lost five hours to an image the model provider refuses, because the refusal arrives as a 502 and the runtime retries it until the time limit.
