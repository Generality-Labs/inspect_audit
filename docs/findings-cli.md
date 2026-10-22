# The findings CLI

`inspect-audit-findings` runs the deterministic producers (inspect-evals-lint, inspect-dataset, `.eval` header checks) over named Inspect Evals evals and writes immutable run files, a `current.json` view per eval, parquet and markdown summaries. Producers run in their own `uvx` environments; override with `INSPECT_AUDIT_LINT_CMD`, `INSPECT_AUDIT_DATASET_CMD`, `INSPECT_AUDIT_HAWK_CMD`.

```bash
uv run inspect-audit-findings run --root ../inspect_evals --out out/ inspect_evals/stereoset
uv run inspect-audit-findings run --root ../inspect_evals --out out/ --featured --logs hawk:<eval-set-id>
uv run inspect-audit-findings summary out/          # re-render from the current view
uv run inspect-audit-findings hawk-sets inspect_evals/scicode
uv run inspect-audit-findings hawk-pull             # fetch scripts/hawk-artefacts.yaml into artefacts/ (gitignored)
```

Limitations today: dataset scans need the eval's HuggingFace path from `eval.yaml` plus a per-eval override table, and skip otherwise; header checks compare against the checkout's `eval.yaml` and take each log at face value (mock and variant runs are not yet filtered); no suppressions or issue links are applied yet. See [roadmap.md](roadmap.md) and the [design spec](superpowers/specs/2026-09-25-findings-prototype-design.md).
