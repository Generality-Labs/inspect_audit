# Leads

Where the findings store meets the investigator agent. The `leads` command and the import are findings-store code; staging the leads into the agent's inputs and the skill text that tells it how to use them are agent code.

`inspect-audit-findings leads --store LOCATOR EVAL` renders one eval's current findings as `LEADS.md`: hypotheses with a location attached, for an agent to confirm or retire. It is the same current view the summaries use, with the review decisions applied. Suppressed observations are dropped and counted; accepted issues are listed first; groups are capped at three examples with a pointer to the run file for the rest; checks that skipped are listed so absence is not read as clean. Every example carries its record id (`<run id>/<n>`), which is what an agent should cite when a register finding rests on a lead.

`--sample ID` narrows to observations whose locations name that sample (dataset sample locations and transcript locations both count), which is what the per-sample auditor wants. `--write PATH` writes the file instead of printing it.

## How agents get leads

Two paths, same content. The pull path is the target; the staged file is the fallback.

- **MCP server (target).** The investigator and sample auditor attach the findings MCP server, which sits on the Store API. `leads(eval, sample_id=None)` returns what `LEADS.md` renders; `finding`, `search`, `issues` and `inputs` let the agent drill in. Write tools `set_status(record_id, status, evidence, reason)`, `accept` and `suppress` let it confirm or retire a lead with provenance set to its run id, so the loop closes live. The server records what it served into the run's inputs for reproducibility. Local runs use stdio MCP over the checkout; Hawk runs need the host on the egress allowlist and a scoped token in the runner. See [architecture.md](architecture.md).

- **Staged file (built 2026-10-12).** Render the leads from the store and pass the file to the investigator:

  ```bash
  set -a; source <credentials file>; set +a      # when the store is a bucket
  uv run inspect-audit-findings leads --store s3://<bucket> inspect_evals/<eval> --bundle leads/
  uv run inspect eval inspect_audit/investigate -T repo=<checkout> -T target_task=inspect_evals/<task> \
      -T leads=leads/ -T execution=local ...
  ```

  `--bundle` writes `LEADS.md` and the eval's current run files under `runs/`, which the file's "and N more in runs/…" pointers name. `investigate` stages the bundle (or a bare `LEADS.md`) read-only under `/inputs/findings/` and names `LEADS.md` in `seed.json` (`leads`), and the investigating skill tells the agent to treat each lead as a hypothesis with a location attached, confirm or retire it, and cite its record id as an evidence location when a register entry rests on it. The file is a snapshot of what the run saw. It is local, so a Hawk run needs it on the runner; the sample auditor's per-sample `/audit/leads.md` is not built yet.

The MCP attach and the sample auditor's staging still touch James's code and prompts and go to him as one PR. The leads module imports nothing from the investigator.

**Investigations are not leads.** An imported investigation's findings and assessments are left out of `LEADS.md` and the bundle's run files, and the statuses its citations set on leads are ignored there (suppressions, issue links and people's statuses still apply), so a re-audit starts from the deterministic observations rather than an earlier verdict.

**Closing the loop.** New investigations write status history through the server. The two investigations that predate it (chess, SciCode) are imported by the register adapter, which reads cited record ids from evidence and writes the status history then.
