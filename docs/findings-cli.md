# The findings CLI

`inspect-audit-findings` runs the deterministic producers (inspect-evals-lint, inspect-dataset, `.eval` header checks) over named Inspect Evals evals and writes immutable run files, a `current.json` view per eval, parquet and markdown summaries. Producers run in their own `uvx` environments; override with `INSPECT_AUDIT_LINT_CMD`, `INSPECT_AUDIT_DATASET_CMD`, `INSPECT_AUDIT_HAWK_CMD`.

```bash
uv run inspect-audit-findings run --root ../inspect_evals --out out/ inspect_evals/stereoset
uv run inspect-audit-findings run --root ../inspect_evals --out out/ --featured --logs hawk:<eval-set-id>
uv run inspect-audit-findings summary out/          # re-render from the current view
uv run inspect-audit-findings hawk-sets inspect_evals/scicode
uv run inspect-audit-findings hawk-pull             # fetch scripts/hawk-artefacts.yaml into artefacts/ (gitignored)
```

`--config PATH` names the per-eval declaration of what to scan and which logs count; the default is the packaged `pilot.yaml`. Each entry can declare the dataset path, config, split, revision and field roles, a `task_args` filter for logs, and whether mock-model runs count (they do not by default). An eval without an entry falls back to `eval.yaml`'s HuggingFace asset for the dataset and uses every non-mock log it matches. Every run records what it examined under `inputs`, and each eval's `SUMMARY.md` has an Inputs section listing the dataset scanned, the logs used and excluded with reasons, and the revision the header checks compared against.

`--review DIR` names the directory holding `suppressions.yaml` and `issues.yaml`, default the out directory; see [review-files.md](review-files.md). Summaries group observations by rule with a count and two examples, and list suppressions and accepted issues.

Limitations today: dataset scans need a declaration or an `eval.yaml` asset and skip otherwise; the header checks compare against the checkout's `eval.yaml`. See [roadmap.md](roadmap.md) and the [design spec](superpowers/specs/2026-09-25-findings-prototype-design.md).
