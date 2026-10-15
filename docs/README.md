# Documentation

User-facing documentation will become a site (Quarto or GitHub Pages). Until then this index sorts the docs by which part of inspect_audit they describe, and by audience.

## The two parts of inspect_audit

inspect_audit holds two pieces of software that grew separately and now meet in two places.

|                  | The agents                                                                                                                                                                                         | The findings store                                                                               |
| ---------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------ |
| What it is       | LLM agents that audit a benchmark: the **sample auditor** judges a benchmark's logged samples one by one; the **investigator** examines a benchmark's source, data and logs and publishes a report | A store of audit records from many producers, with review, rendering, an export and a table site |
| How you run it   | `inspect eval inspect_audit/audit …`, `inspect eval inspect_audit/investigate …` (locally in Docker, or on Hawk)                                                                                   | `inspect-audit-findings …`                                                                       |
| What it produces | An investigation folder with a published bundle (`findings.json`, `assessments.json`, `coverage.json`, a LaTeX report and evidence), outside any store                                             | Run records and review decisions in a store, and the files rendered from them                    |
| Code             | `src/inspect_audit/` outside `findings/`: `_registry.py` (`audit`), `_investigate.py` (`investigate`), `_report.py`, `_jobs.py`, `investigation/`, `skills/`, `prompts/`, `containers/`            | `src/inspect_audit/findings/`                                                                    |
| Costs money      | Yes: model calls                                                                                                                                                                                   | No: the deterministic producers run locally                                                      |

Where they meet:

- **Leads** go from the store to the investigator: `inspect-audit-findings leads --bundle DIR`, then `-T leads=DIR`. The investigator cites the leads it relies on.
- **Import** goes from the investigator to the store: `inspect-audit-findings import-investigation` turns a published bundle into one run record and marks the leads it cited.

The investigator is one producer among several from the store's point of view; the store never runs it.

## The findings store

- [From a sweep to the site: an end-to-end walk-through](end-to-end.md): every command, file and record, step by step, for one eval, including the investigator's place in it. Start here.
- [The findings CLI](findings-cli.md): every subcommand and flag of `inspect-audit-findings`.
- [Review decisions](review-files.md): suppressing, accepting, linking and setting status.
- [Architecture](architecture.md): the store and the renderings built from it.
- [Finding schema: envelope and verbatim source](finding-schema-envelope.md): the store's record types and their fields.
- [Finding schema](finding-schema.md): superseded; the derivation notes behind the envelope.

## The agents

Not yet documented here. The `investigate` task's docstring in `src/inspect_audit/_investigate.py` describes every parameter, and the agents' instructions are their skills (`src/inspect_audit/investigation/skills/`, `src/inspect_audit/skills/`) and prompts (`src/inspect_audit/prompts/`).

## Where the two meet

- [Leads](leads.md): the store's findings as starting points for the investigator, and how its citations come back.
- [Methodology, template and records](methodology.md): what the investigator checks, separate from how its report looks, and the records it should produce for the store; [methodologies/](methodologies/) holds the methodology files.
- Steps 4 to 6 of the [end-to-end walk-through](end-to-end.md).

## Deployments

- [Generality](deployments/generality.md): one organisation's bucket, site, credentials, access and Hawk setup. An example, not a requirement.

## Design notes and planning

- [Roadmap](roadmap.md): the Q4 plan for both parts.
- [The maintenance loop](maintenance-loop.md): a separate track, mining rules from Inspect Evals PR triage.
- [Findings prototype and roadmap review](findings-prototype-review.md): the findings store's first-release review.
- [superpowers/](superpowers/): specs and implementation plans, one per piece of work.
