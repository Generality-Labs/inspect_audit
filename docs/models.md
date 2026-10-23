# Model configuration

Model names use Inspect's full provider/model identity. For example, `openrouter/anthropic/claude-haiku-4-5` selects OpenRouter; `anthropic/claude-haiku-4-5` selects the native Anthropic provider. Names are passed unchanged to both local Inspect jobs and Hawk. Permissions match the entire name. Hawk child configs explicitly select the tested Inspect 0.3.266 release, overriding the deployment's default Inspect pin.

The supported shared-budget setup routes all model calls through one capped OpenRouter key. Local jobs use the configured environment credentials; Hawk jobs must route through the assigned key in Middleman. Merely declaring a model name does not ensure different providers share an allowance. `budget()` reads the configured key balance, not an attributed audit spending total.

## Fixed child model settings

An audit config can declare native Inspect `ModelConfig` records, keyed by full model name:

```yaml
model_configs:
  openrouter/anthropic/claude-haiku-4-5:
    config:
      temperature: 0.2
      max_tokens: 4096
    args: {}
```

`base_url` can also be declared. Keep credentials in the environment or Hawk `child_secrets`, rather than model configuration. These settings are frozen in the launch policy and applied to child subject models, investigators and judge roles. The auditor's own model settings are supplied through Inspect's evaluation options.

When investigation inputs contain recorded judge settings, the investigation preserves them and refuses conflicting declared settings. Multi-model judge roles are not supported.

## Prices and allocation

Investigation cost limits use Inspect cost metadata when available. OpenRouter routes can look up published prices. For a model without cost metadata, declare per-million rates under `model_prices`:

```yaml
model_prices:
  openrouter/example/model:
    input: 1.0
    output: 3.0
```

Inspect sample cost limits are additional controls, not one allowance shared across independent jobs. The auditor decides how to allocate its available budget, including reporting. The same auditor retains its context during publication. Experiments finish before findings are submitted. There is no fixed reporting reserve or automatic key minting requirement.

## Hawk installations

Use the `remote` extra for the operator CLI. Hawk child jobs use the `runner` extra, which installs Hawk client dependencies without the CLI extra. This avoids reintroducing a deployment's CLI-specific Inspect dependency into the child environment. Both use Hawk's existing client and download helpers.

The current deployment remains on Hawk 3.7.2. Passing compatibility probes there does not validate Hawk 4 restricted credentials. The remaining migration work is tracked in [Hawk 4 migration](hawk-4-migration.md).
