# Findings prototype: acceptance run, 2026-09-25

Command, run from the inspect_evals checkout at 5687c5cdf (23 dirty files) with the branch's wheel overlaid:

```text
uv run --with dist/inspect_audit-0.0.1-py3-none-any.whl inspect-audit-findings run --root . --logs logs --out <here>/out \
  inspect_evals/stereoset inspect_evals/hle inspect_evals/agentharm inspect_evals/xstest inspect_evals/strong_reject inspect_evals/simpleqa
```

Wall time 1 min 44 s including the first `uvx` builds of both producers. Exit code 1 because five dataset scans skipped. 236 local logs matched the six evals. Outputs are under `out/`; files over 5 MB (`inspect-evals-stereoset/dataset.run.json`, 8 MB of `answer_length` rows) were deleted before commit, so `summary` regeneration for StereoSet will lack the dataset run.

## Per eval

| Eval | Header | Lint | Dataset |
| --- | --- | --- | --- |
| stereoset | 14 findings: 13 `dataset_samples` (4299 declared vs 2123 / 2115 / 2106 logged), 1 `version_drift` | 1: `IEBP008` duplicate filter unacknowledged | 2,169: 2,123 `answer_length` noise, 28 `inconsistent_format` noise, 18 `duplicate_questions` real |
| hle | 128: 120 `dataset_samples` (2500 declared vs 1 / 2 / 100 / 668 logged), 4 `unscored_samples`, 3 `dirty_revision`, 1 `version_drift` over 10 versions | 3: `IECQ001` private import of `inspect_ai._util.registry`, 2 `IEBP001` `get_model()` in `_resolve_grader` | skipped: `Unknown split "train". Should be one of ['test']` |
| agentharm | 7: 3 `dataset_samples` (176 vs 44), 3 `dirty_revision`, 1 `version_drift` | 140: 138 `IETS007` untested `@tool` functions, 2 `IEBP001` | skipped: `Config name is missing. Please pick one among ['harmless_benign', 'harmful', 'chat']` |
| xstest | 11: 10 `unscored_samples` (`model_graded_qa` left 2 of 2 unscored), 1 `version_drift` | 0 | skipped: `Unknown split "train"` |
| strong_reject | 35: 23 `dataset_samples` (324 declared vs 313 logged, every log), 11 `unscored_samples`, 1 `version_drift` | 1: `IEBP002` allowlisted grader role | skipped, correctly: `external_assets` is a `direct_url`, not HuggingFace |
| simpleqa | 1: `version_drift` over 4 versions | 1: `IEBP002` | skipped: field auto-detection failed on `problem` / `answer` columns |

## What is real

- **StereoSet `IEBP008` and the 18 `duplicate_questions` rows** are the known defect (inspect_evals#2524) from two sides. The header producer adds the third side: `eval.yaml` declares 4,299 samples and no log has ever recorded that number.
- **strong_reject 313 versus 324.** Every one of 23 logs records 313 samples against a declared 324. That is either a wrong `dataset_samples` or eleven rows lost in loading. It was not on anyone's list. Worth a look.
- **HLE `IECQ001`.** A private import of `inspect_ai._util.registry` in the HLE package. Real, and the kind of thing the maintenance agent could fix unattended.
- **`dirty_revision`** on 3 HLE and 3 AgentHarm logs: those logs were produced from uncommitted code and cannot be reproduced from any commit.
- **agentharm 138 untested tools.** Each row is a distinct site, so it is not noise in the StereoSet sense, but nobody wants 138 bullets. It needs grouping by rule in the rendered view.

## What is noise, and why

- **`answer_length` and `inconsistent_format` on StereoSet (2,151 rows).** The answer column is a struct. Already filed as inspect_dataset#26. The prototype makes the volume visible, which was the point, but the summary's "Noise" section only catches rules over 100 findings, so `inconsistent_format` at 28 slips through.
- **`dataset_samples` on HLE (120 rows) and AgentHarm (3).** The 1-, 2- and 100-sample HLE logs and the 44-sample AgentHarm logs are variant runs: the task was called with filtering arguments, so the dataset was smaller by design. The check compares against the default task's declaration without looking at `task_args_passed`. It also emits one finding per log where one per distinct `(declared, actual)` pair with related locations would do.
- **`unscored_samples` on HLE, xstest, strong_reject (25 rows).** Every one is a `mockllm/model` log: a test run where the grader is a mock and returns nothing gradeable. The inspect_evals `logs/` directory is a dev scratch area and most of its 1,978 logs are mock runs. A producer over real logs would not see these; a producer over this corpus needs to know which logs are tests.
- **`version_drift` on all six.** Trivially true of a directory of dev logs spanning two months. Right as an outcome, wrong as a `minor` finding on a corpus like this.

## What the header adapter missed

- `eval.model == "mockllm/model"` as a first-class signal. It would have removed every `unscored_samples` row and most `dataset_samples` rows here.
- `task_args_passed` non-empty as "this is a variant run, compare against the variant". The HLE logs record their arguments; the check ignored them.
- Grouping. Five rules over 236 logs produced 196 findings, most of them one row per log for the same fact.
- `duration_s` is not set on header runs (NaN in `runs.parquet`). Trivial.

## What the envelope could not express

- **Corpus provenance.** Nothing on `Subject` or `Run` says "these logs are dev runs under mockllm". `Run.inputs.logs` lists paths; a `Run.inputs.models` or a per-log `model` in the `log` location would let a consumer filter.
- **One finding, many logs.** `header.version_drift` did this correctly (one finding, one primary, N related locations) and `dataset_samples` did not. The schema supports it; the adapter did not use it.
- **Skip reasons are tracebacks.** The dataset skips carry the last 1,500 characters of stderr. Useful for debugging, useless in a table. The producer should extract the exception line.

## Producer bugs surfaced

- inspect-dataset needs the HF `--split` from the eval, not a `train` default: HLE and XSTest are `test`-only. The eval's own `hf_dataset(..., split=...)` call knows this; `eval.yaml` does not record it.
- inspect-dataset needs the HF `--config` for multi-config datasets (AgentHarm). Same source of truth.
- inspect-dataset field auto-detection misses `problem` (SimpleQA Verified). Already inspect_dataset#27 in spirit.
- Empty `inspect_dataset_*` temp directories are left under `out/` for every skipped scan.
- The per-eval summary's Dataset row is blank when the header run sorts first, because the renderer takes `runs[0].subject`.

## What the aggregator needs first

1. **A corpus filter on `eval.model` and `task_args_passed`.** Without it three of the five header checks are dominated by test logs and variant runs. This is a header-adapter change plus a `Run.inputs` field, not an aggregator feature, and it removes roughly 150 of 196 header findings here.
2. **Grouping by rule in the rendered view, with the count and one example.** 138 AgentHarm tool rows, 120 HLE count rows and 2,123 StereoSet length rows all want the same treatment. The parquet already supports it; the renderer does not.
3. **Suppressions with a reason, applied before rendering.** `answer_length` on struct-typed answers is the first entry. The schema has the field; nothing writes it yet.
4. **Dataset scan arguments derived from the eval's own `hf_dataset` call** (path, config, split, revision) rather than from `eval.yaml` plus a hand-written override table. That closes three of the five skips and fills `subject.dataset.revision`.
5. **Exception-line extraction for skip messages**, so a skipped producer reads as one sentence in a table.

The strong_reject 313 versus 324 discrepancy is the one lead to hand to a person now.
