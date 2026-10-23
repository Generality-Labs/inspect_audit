# Investigator web search

Pass Inspect's provider configuration to `investigation_task` or the registered `investigation` task:

```python
investigation_task(..., search={"tavily": True})
```

In an eval-set task's arguments:

```yaml
search:
  tavily: true
```

This adds Inspect's `web_search` tool. Configure `TAVILY_API_KEY` in the Inspect runner environment. `exa` and `google` are also supported by Inspect and require their own credentials. These external providers work across investigator models. Search requests execute in the runner; the key does not need to enter the investigator's sandbox. External search billing is separate from the investigator's model cost cap.

Native search configurations such as `{"openai": True}` require a model provider that supports them. The installed Inspect OpenRouter adapter does not expose native search; an OpenAI model name routed through it does not enable OpenAI search.

Without `search`, the investigator has shell-based retrieval only. Search results help locate sources; the gold-answer check still requires source evidence. The prompt instructs the investigator to recognise consent pages and challenges and change retrieval methods when access fails.

The full auditor's launch policy does not yet expose this option for its child investigations. This option currently applies to directly configured investigation tasks.
