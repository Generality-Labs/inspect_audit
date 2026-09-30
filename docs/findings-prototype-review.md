# Findings prototype and roadmap review

Date: 2026-09-29. Status: proposal. Reviewed baseline: `findings-prototype` at `eb4ea299b4f3a77f83ff6b75998e61f82fa6cad3`, including the recent formatting and tooling updates.

This reviews the [Q4 roadmap](roadmap.md), the schema proposals, the prototype design and implementation plan, and the available acceptance outputs. It proposes a smaller delivery sequence for the Inspect Evals maintenance work. Implementation of the recommendations is subsequent work.

## Recommendation

Make the next release produce a short, reproducible list of maintenance issues that maintainers use. Correct input selection, preserve the meaning of investigation records, and establish how results survive reruns. Publish a static pilot once its usefulness has been measured. Add voting and expand investigations when their requirements and costs are understood.

Contributor-policy changes need a separate decision after coverage and maintainer workload have been measured. A missed finding can result from unsupported input, a failed producer, or a defect class the audit does not cover.

## Keep the existing approach

- Keep inspect-evals-lint, inspect-dataset, and Auditor separate, with adapters reading producer outputs.
- Keep the [envelope and native producer record](finding-schema-envelope.md) together so findings retain their evidence and provenance.
- Keep the separation between parsing saved records and executing producers. It makes adapter behaviour testable without running an audit.
- Generate summaries deterministically from saved results.
- Keep taxonomy identifiers versioned so historical records retain their original meaning.
- Run inexpensive checks broadly and measure the incremental value and cost of bounded investigations before expanding them.
- Require human review before changing the state of a contributor issue.

## Changes to make first

### 1. Establish which inputs each check applies to

The recorded 2026-09-25 acceptance sweep had five of six dataset scans skip. Of StereoSet's 2,169 dataset findings, 2,151 were identified as scanner noise. Many header findings came from mock runs or task variants with deliberately smaller datasets. These observations concern that saved sweep; a fresh sweep is required to assess later producer changes. The underlying audit outputs remain private.

Fix the comparisons and scanner applicability before applying suppressions. Dataset scans need the task's dataset path, configuration, split, revision, and relevant field mapping. The [current adapter](../src/inspect_audit/findings/adapters/dataset.py) selects the first HuggingFace asset and uses a per-eval override table. Start with a declared configuration for the pilot where inference is unreliable. Define how to obtain these values from the task before extending coverage to dynamic loaders, multiple datasets, and task variants.

Partition logs by task and relevant arguments, and distinguish benchmark attempts from sample-audit and mock runs. Preserve the revision and configuration examined. The [header adapter](../src/inspect_audit/findings/adapters/header.py) currently assigns the current checkout's subject to findings derived from historical logs. Comparisons with current source also need to identify that comparison revision separately.

Show unsupported inputs, failed scans, and unassessed checks alongside findings. Freshness should refer to the last successful check of the stated inputs and revision. Regenerating a page should preserve that date.

### 2. Keep findings, assessments, and coverage distinct

M1 currently proposes converting all investigation registers into envelope findings. The existing records serve different purposes:

| Record                              | Meaning                                                                                      | Proposed treatment                                                            |
| ----------------------------------- | -------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------- |
| `findings.json`                     | Claims about defects, with evidence and a revisable status.                                  | Import into the finding envelope.                                             |
| `assessments.json`                  | Judgments about dimensions and checks, including clean, unassessed, and inapplicable checks. | Retain the assessment model and link it to the same audit.                    |
| `coverage.json` and question labels | The examined population, affected units, unresolved questions, and gaps in coverage.         | Preserve the population and label states, including reconciliation decisions. |

The [assessment](../src/inspect_audit/_assessment.py) and [coverage](../src/inspect_audit/_coverage.py) models already express these distinctions. Render them together with findings. Converting clean or unassessed rows into defect records would require additional interpretation in every consumer.

Use the complete chess bundle for the first integration. Add the reconciled SciCode registers as a second compatibility fixture when available. Keep raw worker judgments distinguishable from the investigator's reconciled results. Neither SciCode availability nor migration of the investigator's native format needs to block the first maintainer view.

### 3. Preserve runs and separate observation identity from issue identity

Three current behaviours affect the planned hosted view and feedback loop:

- [Output writing](../src/inspect_audit/findings/cli.py) reuses producer filenames. A later sweep overwrites earlier runs for selected producers and leaves files from omitted producers in place. Regenerating a summary can therefore reintroduce older results.
- [Parquet export](../src/inspect_audit/findings/io.py) omits suppressions, aliases, effects, and several subject fields. A site reading only this export would lack information the roadmap expects it to use.
- [Fingerprints](../src/inspect_audit/findings/fingerprint.py) include location keys containing code line numbers or evaluation IDs. A moved line or a new evaluation run can give the same underlying problem a different fingerprint.

Start with immutable run directories and a manifest selecting the runs used by a view. Keep JSON authoritative and regenerate Parquet and pages. Store reviewed suppressions and links to GitHub issues in a small separate file so producer reruns preserve those decisions. Scope suppressions to the relevant rule, subject, and applicability conditions, with an author and reason.

Group observations by producer and rule for presentation. That grouping does not establish that they share a root cause. Assign a durable issue identifier when a candidate is accepted, and link its observations to it. Defer automatic cross-producer matching, alias migration, and inferred introduced/fixed versions until a consumer requires them.

### 4. Measure maintenance usefulness

The historical PR reviews named in M2b are a starting point for a test set. Each case needs a defective revision, a fixed revision, required inputs, and a description of the expected defect. Check whether the finding appears before the fix and disappears afterwards. Running only against the current eval may examine code where the defect has already been fixed.

Separate cases detectable by deterministic producers from those requiring investigation. Keep some cases separate from those used to develop rules. Include false positives and skipped coverage in the results.

For the maintainer pilot, record accepted issues, review time, recall, false positives, and cost per accepted new issue. For the bounded investigations, also record wall time and additional findings beyond the deterministic pass. If new benchmark runs are needed to supply logs, account for that cost separately. Before the pilot, agree with maintainers what results would justify expansion.

### 5. Reduce dependencies before publication

The first maintainer view can group by producer and rule while retaining the original taxonomy identifiers. Confirmation of `gl-audit@2` can proceed separately. A later display mapping should preserve the original classification and identify any mapping that requires judgment.

Publish a static index with reviewed findings, coverage, evidence links, and issue drafts. Use GitHub issue reactions for promoted issues initially. A custom voting service and a choice between GitHub Projects and a separate findings database can wait for experience with the pilot.

Add a publication export that selects reviewed records and approved evidence. Native producer records remain available privately; publication must decide which embedded fields and files can be released. Review both the execution environment and the publication process before expanding their respective use.

## Proposed delivery sequence

Replace M1 through M6, including M2b, with four milestones. Set dates after agreeing the pilot scope and acceptance criteria.

| Milestone                        | Work                                                                                                                                                          | Completion criterion                                                                                                                                                               |
| -------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 1. Produce a usable local pilot. | Correct input selection and provenance, retain runs, import the chess bundle, group findings, and apply reviewed suppressions.                                | Maintainers can identify actionable candidates and see the inputs, revision, checks, skips, and unassessed areas. Rerendering preserves the selected results and review decisions. |
| 2. Measure usefulness and cost.  | Test historical defects before and after fixes, and run bounded investigations on two or three evals. Add reconciled audit fixtures as they become available. | Record recall, false positives, successful coverage, review time, and incremental cost against the agreed pilot criteria.                                                          |
| 3. Publish the pilot.            | Generate static pages from reviewed exports and provide issue drafts for the maintenance workflow.                                                            | Evidence links work; pages state the examined revisions, coverage, successful check dates, and review status. Maintainers can promote accepted candidates to issues.               |
| 4. Expand on a schedule.         | Run supported deterministic checks across the registry and investigations at an affordable cadence.                                                           | Monitor successful coverage, stale results, failures, and cost. Each eval page shows which checks apply and which have completed.                                                  |

QA starts with the pilot and continues through each milestone. Validation against manual audits can extend the test set as the corresponding investigation capabilities are added.

## Keep the documentation small

Maintain the roadmap and one short architecture document describing the current contract and supported workflow. Label the [full-normalisation proposal](finding-schema.md) as superseded and the [implementation plan](superpowers/plans/2026-09-25-findings-prototype.md) as historical. Put installation, runnable commands, and current limitations in the README. Continue using skills for agentic investigation and ordinary Python for deterministic adapters and rendering.

The decisions needed now are the pilot evals, the inputs each producer supports, the run-selection and review-file formats, and the acceptance criteria agreed with maintainers. Custom voting, automatic issue closure, taxonomy migration, and a general findings database can remain deferred.
