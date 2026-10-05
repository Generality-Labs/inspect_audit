# Leads

`inspect-audit-findings leads --out DIR EVAL` renders one eval's current findings as `LEADS.md`: hypotheses with a location attached, for an agent to confirm or retire. It is the same current view the summaries use, with the review files applied. Suppressed observations are dropped and counted; accepted issues are listed first; groups are capped at three examples with a pointer to the run file for the rest; checks that skipped are listed so absence is not read as clean. Every example carries its record id (`<run id>/<n>`), which is what an agent should cite when a register finding rests on a lead.

`--sample ID` narrows to observations whose locations name that sample (dataset sample locations and transcript locations both count), which is what the per-sample auditor wants. `--write PATH` writes the file instead of printing it.

## How agents get leads

Two paths, same content. The pull path is the target; the staged file is the fallback.

- **MCP server (target).** The investigator and sample auditor attach the findings MCP server, which sits on the Store API. `leads(eval, sample_id=None)` returns what `LEADS.md` renders; `finding`, `search`, `issues` and `inputs` let the agent drill in. Write tools `set_status(record_id, status, evidence, reason)`, `accept` and `suppress` let it confirm or retire a lead with provenance set to its run id, so the loop closes live. The server records what it served into the run's inputs for reproducibility. Local runs use stdio MCP over the checkout; Hawk runs need the host on the egress allowlist and a scoped token in the runner. See [v1-architecture.md](v1-architecture.md).
- **Staged file (fallback).** `prepare_workspace` writes `/inputs/findings/LEADS.md` from `leads_markdown(out, task, review)` and copies the eval's current run files to `/inputs/findings/runs/`, naming it in `seed.json`; `_item.py` stages `/audit/leads.md` for its one sample beside `discrepancies.md`. Used when the server is unreachable, and as the snapshot of what a run saw.

The attach step, the staging, and one paragraph of skill text ("read the leads, treat each as a hypothesis with a location attached, confirm or retire it and record the status with the record id") touch the investigator and auditor code and prompts, and go to James as one PR. The leads module imports nothing from them.

**Closing the loop.** New investigations write status history through the server. The two investigations that predate it (chess, SciCode) are imported by the register adapter, which reads cited record ids from evidence and writes the status history then.
