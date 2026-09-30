# Leads

`inspect-audit-findings leads --out DIR EVAL` renders one eval's current findings as `LEADS.md`: hypotheses with a location attached, for an agent to confirm or retire. It is the same current view the summaries use, with the review files applied. Suppressed observations are dropped and counted; accepted issues are listed first; groups are capped at three examples with a pointer to the run file for the rest; checks that skipped are listed so absence is not read as clean. Every example carries its record id (`<run id>/<n>`), which is what an agent should cite when a register finding rests on a lead.

`--sample ID` narrows to observations whose locations name that sample (dataset sample locations and transcript locations both count), which is what the per-sample auditor wants. `--write PATH` writes the file instead of printing it.

## Staging contract

These hooks live in the investigator and auditor code and land as a separate PR. The leads module does not import them.

- **Investigator.** `prepare_workspace` gains a `findings` argument naming a findings output directory. It writes `/inputs/findings/LEADS.md` from `leads_markdown(out, task, review)` and copies the eval's current run files to `/inputs/findings/runs/`, then adds `"findings": "/inputs/findings/LEADS.md"` to `seed.json`. The investigating skill gets one paragraph after "Read /inputs/seed.json": read `LEADS.md`, treat each lead as a hypothesis with a location attached, and cite its record id in a register finding's evidence when you confirm or retire it.
- **Sample auditor.** Where `_item.py` stages `discrepancies.md`, it also stages `leads.md` from `leads_markdown(out, task, review, sample_id=<sample>)` when a findings directory is configured. `prompts/audit.md` gets the sentence it already has for `discrepancies.md`: "If `{root}/leads.md` is present, it is worth a look."
- **Closing the loop.** The register-to-envelope adapter (the chess import) reads cited record ids from evidence and writes status history on the cited observation: lead confirmed or retired, with provenance.
