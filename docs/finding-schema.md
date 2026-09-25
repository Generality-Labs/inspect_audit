# A finding schema for eval audits

Proposal, 2026-09-25. One record shape that every producer writes (inspect-evals-lint, inspect-dataset, the inspect_audit sample auditor and investigator, Scout scanners) and every consumer reads (the hosted Inspect Evals issue index, the third-party audit reports, the maintenance agent, the hillclimb harness). It is a defect record, not a measurement record: a Scout `Result` says what was observed in one transcript; a finding says what is wrong with an eval and whether it still is.

The evidence for the design is the deterministic pass over StereoSet in `agent_artefacts/deterministic_pass/stereoset/SUMMARY.md`, where three producers found the same defect from three sides and disagreed on every version identifier.

## Design rules

- **Fields are borrowed, not invented.** Each field names the format it comes from. Where two formats disagree, the reason for the choice is stated.
- **Few required fields.** GitLab's Code Quality format proves six fields are enough to render and deduplicate. Everything else is optional and arrives as producers mature.
- **Absence is recorded.** A producer that checked and found nothing writes a `pass` outcome, as lint does today. A consumer can then distinguish "clean" from "not examined".
- **Versions on every record.** Source revision, dataset revision, package versions. The StereoSet pass found four kinds of version disagreement across five logs; without the subject's revision no finding can be re-checked.
- **Priority and votes live outside the record.** They are computed over findings by fingerprint, in the hosting layer, and change without the finding changing.

## What an Inspect log already records

Before borrowing from outside formats, use what every `.eval` header carries. Read from the StereoSet and CORE-Bench logs on 2026-09-25 with `read_eval_log(header_only=True)`.

| Log field | What it is | How the schema uses it |
| --- | --- | --- |
| `eval.eval_id`, `run_id`, `eval_set_id`, `task_id`, `created` | Identity of the run and of the task instance within it | `run.inputs.logs[]` names logs by `eval_id`, not by path. Paths move; ids do not. |
| `eval.task`, `task_registry_name`, `task_display_name` | Qualified and display names | `subject.eval` is `task_registry_name`. |
| `eval.task_version` | Int or string; inspect_evals writes the comparability integer | `subject.task_version.comparability`. |
| `eval.metadata.full_task_version`, `task_comparability_version`, `task_interface_version` | inspect_evals' `N-X` scheme from TASK_VERSIONING.md, written into every log | `subject.task_version.full`. `introduced` and `fixed` accept a comparability version as well as a commit: a finding that moves scores is bounded by the version that declares scores incomparable, which is the unit maintainers and users already reason in. |
| `eval.task_args` and `task_args_passed`; `plan.steps[].params` and `params_passed` | Resolved values and the subset the operator actually gave | Drift checks compare resolved values (`_concordance.drift` already does). A finding can state whether it concerns a default nobody chose. |
| `eval.revision` `{type, origin, commit, dirty}` | Git identity of the repository the eval ran from, when run from a checkout | `subject.revision.commit`. `dirty: true` becomes a caveat on the finding. |
| `eval.packages` `{inspect_ai, inspect_evals, ...}` | Installed versions; dev builds carry the commit as a PEP 440 local segment (`0.21.1.dev36+gd4199f415`) | `subject.revision` is recoverable from an installed package when `revision` is absent, which is the common case for wheel installs. |
| `eval.dataset` `{name, location, samples, sample_ids, shuffled}` | Dataset identity as Inspect sees it | `subject.dataset.path` and the denominator for sample-count findings. **No revision is recorded**, even though inspect_evals forces `revision=` on every `hf_dataset` call. See open questions. |
| `eval.scorers[]` `{name, options, metrics[{name, options}]}`, `eval.model_roles`, `model_generate_config` | Declared grader and metric identity | `locations[]` of kind `scorer` name the scorer as the log names it, so a grading finding joins to `results.scores[]`. A grader a scorer constructs internally is not here; only `ModelEvent`s show it, which is why the Epoch chess extractor drift was invisible in headers. |
| `plan.steps[]` with tools serialised as registry refs `{type: tool, name, params}` | The declared elicitation | What `_contract` walks. A `scaffold` finding cites `plan.steps[i]`. |
| `results.total_samples`, `completed_samples`, `scores[].scored_samples`, `unscored_samples`; `stats.model_usage` | Denominators and cost | `effect.denominator` and `run.cost_usd` are read, not estimated. |
| `sample.uuid`, `sample.id`, `sample.epoch`; `message.id`; `event.uuid`, `event.span_id` | Globally unique sample identity and addressable transcript parts | The `transcript` location is `{eval_id, sample_uuid, message_id or event_uuid}`, the same triple Scout's `Reference` and Hawk use. `sample.id` is kept for display only: StereoSet's ids changed between task versions 3 and 4, its uuids did not. |
| `EvalLog.invalidated`, `EvalSample.invalidation: ProvenanceData`, `log_updates[]`, `config_updates[]`, `tags`, `metadata` | Inspect's own post-hoc edit and invalidation machinery, each edit stamped `{timestamp, author, reason, metadata}` | `suppressions[]` and status changes reuse the `ProvenanceData` shape rather than inventing one. And the log is a consumer as well as a source: a supported finding that a sample is unscorable can be written back with `invalidate_samples`, with the finding's fingerprint in `reason`, so `inspect view` shows it. |

## The record

Grouped by the question each group answers. Required fields are marked. Source formats: [SARIF 2.1.0](https://docs.oasis-open.org/sarif/sarif/v2.1.0/sarif-v2.1.0.html), [GitLab Code Quality](https://docs.gitlab.com/ci/testing/code_quality/), [OSV](https://ossf.github.io/osv-schema/), [Inspect Scout](https://meridianlabs-ai.github.io/inspect_scout/results.html), [Auto Benchmark Audit](https://arxiv.org/html/2605.26079), [BenchRisk](https://arxiv.org/pdf/2510.21460), the Generality Labs audit framework (`investigation/report/framework/`), inspect_audit's `Finding` and `QuestionAssessment`, inspect-evals-lint's `Diagnostic`, inspect-dataset's `Finding`.

### Identity: is this the same finding I saw last time?

| Field | Req | From | What it buys us |
| --- | --- | --- | --- |
| `id` | yes | inspect_audit `Finding.id`, Scout `Result.uuid` | Unique within one producer run. Cheap to mint, useless across runs. |
| `fingerprint` | yes | SARIF `partialFingerprints`, Code Quality `fingerprint` | Stable hash of `(producer, rule, subject.eval, normalised primary location)`. Lets a re-run say "same finding" without string matching, and is the key the vote store and the maintenance agent use. Producers define the recipe; the aggregator only compares. |
| `aliases` | | OSV `aliases` | Other identities for the same defect: a GitHub issue or PR URL, another producer's fingerprint, an Epoch review anchor. Symmetric and transitive, so three producers seeing one defect collapse into one row. |
| `baseline_state` | | SARIF `baselineState` | `new`, `unchanged`, `updated`, `absent` relative to a named prior run. Set by the aggregator, not the producer. This is the field the "run audit before and after a fix" workflow reads. |

### Subject: what is this a finding about?

| Field | Req | From | What it buys us |
| --- | --- | --- | --- |
| `subject.eval` | yes | Inspect registry name | `inspect_evals/stereoset`. The join key for the hosted index. |
| `subject.task_args` | | inspect_audit `AuditItem.task_args` | Findings about `task_type=intrasentence` are not findings about the default variant. |
| `subject.revision` | yes | Inspect `eval.revision` and `eval.packages`, OSV `affected.ranges` (git), Scout `scan_git_commit` | `{commit, package_version, dirty}` of the code examined. Required because the StereoSet pass showed the same eval at v3 and v4 has different sample ids and counts. Derivable from a log header even for wheel installs. |
| `subject.task_version` | | Inspect `eval.task_version`, inspect_evals `metadata.full_task_version` | `{full: "4-A", comparability: 4, interface: "A"}`. The unit inspect_evals already uses to say when scores stop being comparable. |
| `subject.dataset` | | Inspect `eval.dataset`, inspect-dataset `scan_summary` (`dataset_name`, `revision`, `config`, `split`) | Which data was looked at. `revision` is `null` when unknown, which for logs is always, since Inspect does not record it. |
| `introduced`, `fixed` | | OSV `introduced` / `fixed` events, inspect_evals comparability versions | `{commit}` or `{comparability_version}` bounding the defect. `fixed` is set when a later run reports `baseline_state: absent` and a human confirms, or by the maintenance agent's PR. Turns "is this issue still open" into a version comparison a log header can answer. |

### Classification: what kind of problem, and who owns it?

| Field | Req | From | What it buys us |
| --- | --- | --- | --- |
| `rule.producer` | yes | SARIF `tool.driver.name`, Scout `scanner_name` | `inspect-evals-lint`, `inspect-dataset`, `inspect_audit/audit`, `inspect_audit/investigate`, `inspect_scout`. |
| `rule.code` | yes | lint rule codes (`IEBP008`), Code Quality `check_name`, Scout `scanner_key` | Machine name of the check. For agentic producers it is the skill or item name (`gold-answer`, `question-labels`). |
| `rule.version` | | Scout `scanner_version`, `scanner_package_version` | Which version of the check produced this, so a rule change explains a finding appearing or vanishing. |
| `dimension` | yes | GL framework nine dimensions: `construct`, `content_validity`, `dataset`, `scaffold`, `harness`, `environment`, `grading`, `resources`, `informativeness` | One taxonomy shared with the third-party reports so a hosted index and a LaTeX report count the same way. ABA's three axes and the ABC checklist map onto it; James's catalogue already did that mapping for the audit items. |
| `check` | | GL framework check ids (`G.4`) via `_assessment.framework_checks` | Finer than dimension where the producer knows it. Optional because lint and dataset rules will not all map cleanly. |
| `mechanism` | | red-teaming skill `mechanism`, ABA "why it matters" | One line naming the causal hole, so many instances of one defect group into one finding with many demonstrations. |

### Location: where is it, exactly?

| Field | Req | From | What it buys us |
| --- | --- | --- | --- |
| `locations[]` | yes, at least one | SARIF `locations` and `relatedLocations`, Scout `Reference`, inspect_audit `EvidenceRef` | A typed union. Every producer already emits one of these shapes; the schema just names them. |
| `locations[].kind` | yes | | `code` (file, line, end_line, column: lint, SARIF physical location), `sample` (dataset, sample_id, field: inspect-dataset), `transcript` (eval_id, sample_uuid, message_id or event_uuid, plus sample_id and epoch for display: Scout `Reference`, Inspect sample and event ids), `log` (eval_id, JSON path into the header such as `eval.dataset.samples` or `plan.steps[1]`), `scorer` (eval_id, scorer name as in `eval.scorers[]`), `artifact` (path, location: inspect_audit `EvidenceRef` for scripts, tables and figures), `url`. |
| `locations[].quote` | | inspect_audit `EvidenceRef.quote`, Scout `Reference.cite` | Verbatim text at the location. The audit prompts insist on this because a summary normalises exactly the detail that is the finding. |
| `locations[].role` | | SARIF `relatedLocations` | `primary` or `related`. The first primary location feeds the fingerprint. |

### Claim: what is wrong and how do we know?

| Field | Req | From | What it buys us |
| --- | --- | --- | --- |
| `summary` | yes | Code Quality `description`, SARIF `message` | One sentence, renderable in a table row. |
| `description` | | ABA "Claim & Why it Matters" | The mechanism and its consequence for interpreting the score. |
| `reproduce` | | inspect_audit `Finding.reproduce` | Command or script that shows it again. Deterministic producers fill this with their own invocation. |
| `suggested_fix` | | ABA "Suggested Fix", lint `Diagnostic.hint` | What to change. Feeds the maintenance agent's brief. |
| `status` | yes | inspect_audit `Finding.status` | `hypothesis`, `supported`, `qualified`, `retracted`. Deterministic producers emit `supported`. Agent producers start at `hypothesis`. Retracted findings stay in the record so the hillclimb harness can count false positives. |
| `origin` | | inspect_audit `Finding.origin` | `source`, `dataset`, `historical` (recorded logs), `experiment` (a run we commissioned), `audit_limitation`. Separates "the benchmark is broken" from "our audit could not check". |
| `evidence_provenance` | | inspect_audit `BenchmarkState` provenance mix | For findings backed by a graded attempt: counts of `real`, `enacted`, `authored` messages. A grade earned after an authored tool result is a weak claim, and the record should say so. |

### Size: how much does it matter?

| Field | Req | From | What it buys us |
| --- | --- | --- | --- |
| `severity` | yes | GL framework scale: `none`, `minor`, `major`, `critical` | Same scale as the dimension assessments, so a report's scorecard and the hosted index agree. inspect-dataset's low/medium/high and lint's warning/error map onto it in the adapter, and the mapping is written down there. ABA's 0/1/2 is the same three steps. |
| `effect.affected` , `effect.denominator` | | writing skill's denominator rule, inspect_audit `CheckAssessment.affected_ids` | "8 of 2,123 records" rather than a percentage. Absent when unmeasured, never estimated. |
| `effect.score` | | inspect_audit `QuestionAssessment.scoring_effect` | `measured` or `hypothesised`, plus a description. The org plan wants to know which findings move the number. |
| `likelihood`, `scale` | | BenchRisk | Optional second and third axes. Not required for v1; noted so severity does not quietly absorb them. |

### Provenance: who produced this, when, from what?

| Field | Req | From | What it buys us |
| --- | --- | --- | --- |
| `run.id`, `run.timestamp` | yes | Scout `scan_id`, `timestamp` | Every finding can be traced to one invocation. |
| `run.producer_version`, `run.git_commit` | yes | Scout `scanner_package_version`, `scan_git_commit` | Which build of the producer. |
| `run.inputs` | | Inspect `eval_id` / `eval_set_id`, Scout `transcript_source_*`, inspect_audit `seed.json` | Logs by `eval_id` (with a location hint), Hawk eval set ids, dataset revision, source commit. Mirrors the subject fields but records what was actually read. |
| `run.model`, `run.cost_usd` | | inspect_audit `budget`, Scout `transcript_model` | Null for deterministic producers. The org plan needs cost per eval before rollout. |
| `suppressions[]` | | SARIF `suppressions`, lint allowlist `key`, catalogue `by-design`, Inspect `ProvenanceData` | A finding judged not a defect is kept and marked, each entry `{kind, provenance: {timestamp, author, reason}}`, the same shape Inspect stamps on log edits and sample invalidations. StereoSet's 2,123 `answer_length` rows should become one suppressed finding with a reason, not disappear. |
| `history[]` | | Inspect `log_updates[]`, SARIF `baselineState` | Status changes with `ProvenanceData`. A retraction says who retracted it and why. |

## Container

A **run** is a JSON document: a header (the `run` block above, plus `outcomes[]` of `{rule, status: pass|fail|skip}` so absence is recorded) and `findings[]`. Producers write one file per run. The aggregator stores findings as parquet keyed by fingerprint with `baseline_state` computed against the previous run for the same `subject.eval`. This is Scout's storage shape with a defect record in it.

Two exports are cheap and worth doing early:

- **SARIF** for the code-located subset, so GitHub renders lint and source findings on PRs in inspect_evals.
- **Code Quality JSON** as the smallest interchange form, for anyone who wants a table.

## Worked example

The StereoSet duplicate-id defect, as one finding aggregated from three producers. Each producer emitted its own record; the aggregator merged them on `aliases` after a human confirmed they were the same defect.

```json
{
  "schema_version": "0.1",
  "id": "stereoset-dup-ids",
  "fingerprint": "sha256:6f1c…",
  "aliases": [
    "https://github.com/UKGovernmentBEIS/inspect_evals/pull/2524",
    "inspect-evals-lint:IEBP008:src/inspect_evals/stereoset/stereoset.py",
    "inspect-dataset:duplicate_questions:McGill-NLP/stereoset:intersentence"
  ],
  "baseline_state": "unchanged",
  "subject": {
    "eval": "inspect_evals/stereoset",
    "task_args": {"task_type": "intersentence"},
    "revision": {"commit": "5687c5cdf", "package_version": "0.21.1.dev24+g5687c5cdf", "dirty": false},
    "task_version": {"full": "3-A", "comparability": 3, "interface": "A"},
    "dataset": {"path": "McGill-NLP/stereoset", "config": "intersentence", "split": "validation", "revision": null}
  },
  "introduced": null,
  "fixed": {"commit": "d4199f415", "comparability_version": 4},
  "rule": {"producer": "inspect_audit/investigate", "code": "dataset-identity", "version": "0.0.1"},
  "dimension": "dataset",
  "check": "C.2",
  "mechanism": "content-hash sample id built from a subset of fields plus a keep-first duplicate filter",
  "locations": [
    {"kind": "code", "role": "primary", "file": "src/inspect_evals/stereoset/stereoset.py", "line": 64, "column": 15,
     "quote": "filter_duplicate_ids(dataset)"},
    {"kind": "sample", "role": "related", "dataset": "McGill-NLP/stereoset", "sample_id": "2a994bc105c63ddfdd912f52cbf8c63a",
     "quote": "cameroon is a country in africa."},
    {"kind": "log", "role": "related", "eval_id": "4DBdwkxMiueonBoKHNQGUL", "path": "eval.dataset.samples", "quote": "2123",
     "location_hint": "logs/2026-09-24T04-02-11-00-00_stereoset_h4nAuEmRrLX897VjquAUw2.eval"},
    {"kind": "code", "role": "related", "file": "src/inspect_evals/stereoset/eval.yaml", "line": 11, "quote": "dataset_samples: 4299"}
  ],
  "summary": "8 of 2,123 intersentence records are silently dropped because the sample id identifies the context, not the record.",
  "description": "Records sharing context, target and bias_type but with different candidate sentences and gold labels collapse to one id; filter_duplicate_ids keeps the first. The evaluated set is 2,115 not 2,123, and eval.yaml reports 4,299, which matches neither variant.",
  "reproduce": "python -c 'from inspect_evals.stereoset.stereoset import stereoset; d=stereoset(shuffle=False).dataset; print(len(d), len({s.id for s in d}))'",
  "suggested_fix": "Use the dataset's own per-record uuid as the sample id; correct dataset_samples.",
  "status": "supported",
  "origin": "source",
  "severity": "minor",
  "effect": {"affected": 8, "denominator": 2123, "score": {"kind": "hypothesised", "description": "scored population shrinks by 0.4%; direction unknown"}},
  "run": {"id": "det-2026-09-25-stereoset", "timestamp": "2026-09-25T04:20:50Z", "producer_version": "0.0.1",
          "git_commit": "8c2748b",
          "inputs": {"logs": [{"eval_id": "4DBdwkxMiueonBoKHNQGUL", "location": "logs/2026-09-24T04-02-11-00-00_stereoset_h4nAuEmRrLX897VjquAUw2.eval"}],
                     "source": "5687c5cdf"},
          "model": null, "cost_usd": 0.0},
  "suppressions": [],
  "history": [{"status": "supported", "provenance": {"timestamp": "2026-09-25T04:20:50Z", "author": "inspect_audit/investigate", "reason": "deterministic pass"}}]
}
```

And a suppressed noise finding from the same pass, kept rather than deleted:

```json
{
  "id": "stereoset-answer-length",
  "fingerprint": "sha256:a0b3…",
  "subject": {"eval": "inspect_evals/stereoset", "revision": "5687c5cdf",
              "dataset": {"path": "McGill-NLP/stereoset", "config": "intersentence", "split": "validation", "revision": null}},
  "rule": {"producer": "inspect-dataset", "code": "answer_length", "version": "0.x"},
  "dimension": "dataset",
  "locations": [{"kind": "sample", "role": "primary", "dataset": "McGill-NLP/stereoset", "sample_id": "bb7a8bd19a8cfdf1381f60715adfdbb5"}],
  "summary": "2,123 of 2,123 answers exceed 4 words.",
  "status": "retracted",
  "origin": "dataset",
  "severity": "none",
  "effect": {"affected": 2123, "denominator": 2123},
  "suppressions": [{"kind": "not_applicable", "provenance": {"timestamp": "2026-09-25T05:00:00Z", "author": "matt",
                     "reason": "answer column is a struct of three candidate sentences; the scanner assumes a short string"}}]
}
```

## Adapters to write, in order

1. **inspect-evals-lint** `Diagnostic` and `outcomes` to findings and run outcomes. Nearly one-to-one; the only decision is the severity mapping and the dimension for each rule code, which can live in the lint rule registry.
2. **inspect-dataset** `Finding` to findings. Needs `sample` locations and the dataset revision; both are already in `scan_summary.json` and the finding metadata.
3. **inspect_audit** verdicts and `findings.json` to findings. The investigator's `Finding` is the closest ancestor and already has `status`, `origin`, `evidence`, `reproduce`. The sample auditor's `record_verdict` evidence (observed, source) maps to locations with quotes.
4. **Log header checks** as a fourth deterministic producer: `eval.dataset.samples` against `dataset_samples` in `eval.yaml`, logged sample ids and uuids against the resolved dataset, `packages` and `task_version` drift across a corpus, `results.scores[].unscored_samples`, `model_roles` unset where a scorer takes a grader. All of this came out of `inspect_audit._concordance.drift` and `attempts` today with no Docker, and every field is in the header.
5. **Write-back.** A supported sample-level finding can be pushed into the source logs with `invalidate_samples` and a `MetadataEdit` carrying the fingerprint, so the finding is visible in `inspect view` and to `samples_df` without our tooling.

## Open questions

- **Dataset revision is not in the log.** inspect_evals refuses an `hf_dataset` call without `revision=`, but Inspect's `EvalDataset` has no field for it, so the pinned revision never reaches the header and `subject.dataset.revision` cannot be filled from a log. Two fixes, either sufficient: inspect_evals writes the revision into `Task.metadata` (it already writes `full_task_version` there), or `inspect_ai.dataset.hf_dataset` records it on `EvalDataset`. The first is ours to do this week; the second is an upstream PR.

- Whether `dimension` should be required for deterministic producers, or assigned in the adapter from the rule code. Recommendation: assigned in the adapter, from a table in each producer's rule registry, so producers stay taxonomy-agnostic.
- Whether Scout's `Reference` should be widened upstream to cover `code` and `sample`, or whether we keep a superset type here and convert. Recommendation: propose upstream after this schema has two consumers, per the earlier note on Scout PRs.
- Fingerprint recipe for agent-produced findings, where the primary location is a claim rather than a line. Candidate: `(producer, rule, eval, dimension, normalised summary)` with a human merge step via `aliases`.
