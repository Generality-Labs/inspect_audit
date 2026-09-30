# The findings CLI

`inspect-audit-findings` runs the deterministic producers (inspect-evals-lint, inspect-dataset, `.eval` header checks) over named Inspect Evals evals and writes immutable run files, a `current.json` view per eval, parquet and markdown summaries. Producers run in their own `uvx` environments; override with `INSPECT_AUDIT_LINT_CMD`, `INSPECT_AUDIT_DATASET_CMD`, `INSPECT_AUDIT_HAWK_CMD`. The exception is a dataset scan through an eval's task, which imports the eval. It runs as `uv run --project <root> --frozen --with <inspect-dataset> inspect-dataset`, in the checkout's own environment. Override it with `INSPECT_AUDIT_DATASET_TASK_CMD`, where `{ie_root}` stands for the checkout path.

```bash
uv run inspect-audit-findings run --root ../inspect_evals --out out/ inspect_evals/stereoset
uv run inspect-audit-findings run --root ../inspect_evals --out out/ --featured --logs hawk:<eval-set-id>
uv run inspect-audit-findings summary out/          # re-render from the current view
uv run inspect-audit-findings hawk-sets inspect_evals/scicode
uv run inspect-audit-findings hawk-pull             # fetch scripts/hawk-artefacts.yaml into artefacts/ (gitignored)
```

`--config PATH` names the per-eval declaration of what to scan and which logs count; the default is the packaged `pilot.yaml`. By default the dataset is scanned through the eval's first task in `eval.yaml` (`inspect-dataset scan inspect_evals/<task>`). That way the scan sees the split, config, revision, field mapping and sample ids the eval uses. Each entry can declare a different `task`, or HuggingFace settings (dataset path, config, split, revision and field roles) to scan the HuggingFace dataset directly, with `eval.yaml`'s HuggingFace asset as the default path. It can also declare a `task_args` filter for logs, and whether mock-model runs count (they do not by default). An eval without an entry uses every non-mock log it matches. Every run records what it examined under `inputs`, and each eval's `SUMMARY.md` has an Inputs section listing the dataset scanned, the logs used and excluded with reasons, and the revision the header checks compared against.

Limitations today: a task scan covers one task per eval (the first, or the declared one), and it imports the eval's code on the host, as running the eval would; a dataset scan skips when there is no task, asset or declaration; the header checks compare against the checkout's `eval.yaml`; no suppressions or issue links are applied yet. See [roadmap.md](roadmap.md) and the [design spec](superpowers/specs/2026-09-25-findings-prototype-design.md).
