# Auditor web search

Pass Inspect's provider configuration to `audit_task` or the registered `audit` task:

```python
audit_task(..., search={"tavily": True})
```

In an eval-set task's arguments:

```yaml
search:
  tavily: true
```

This adds Inspect's `web_search` tool. Configure `TAVILY_API_KEY` in the Inspect runner environment. `exa` and `google` are also supported by Inspect and require their own credentials. These external providers work across auditor models. Search requests execute in the runner; the key does not need to enter the auditor's sandbox. External search billing is separate from the auditor's model cost cap.

Native search configurations such as `{"openai": True}` require a model provider that supports them. The installed Inspect OpenRouter adapter does not expose native search; an OpenAI model name routed through it does not enable OpenAI search.

Without `search`, the auditor has shell-based retrieval only. Search results help locate sources; the gold-answer check still requires source evidence. The prompt instructs the auditor to recognise consent pages and challenges and change retrieval methods when access fails.

The full investigator's launch policy does not yet expose this option for its child audits. This option currently applies to directly configured audit tasks.
