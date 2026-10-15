# Documentation

User-facing documentation will become a site (Quarto or GitHub Pages). Until then this index separates what a user of inspect_audit reads from the design notes behind it.

## Using inspect_audit

- [From a sweep to the site: an end-to-end walk-through](end-to-end.md): every command, file and record, step by step, for one eval. Start here.
- [The findings CLI](findings-cli.md): every subcommand and flag of `inspect-audit-findings`.
- [Review decisions](review-files.md): suppressing, accepting, linking and setting status.
- [Leads](leads.md): deterministic findings as starting points for the investigator.
- [Architecture](architecture.md): the store and the renderings built from it.

## Reference

- [Finding schema: envelope and verbatim source](finding-schema-envelope.md): the record types and their fields.
- [Finding schema](finding-schema.md): the derivation notes behind the envelope.
- [Methodology, template and records](methodology.md): what the investigator checks, separate from how a report looks; [methodologies/](methodologies/) holds the methodology files.

## Deployments

- [Generality](deployments/generality.md): one organisation's bucket, site, credentials and access. An example, not a requirement.

## Design notes and planning

- [Roadmap](roadmap.md)
- [The maintenance loop](maintenance-loop.md)
- [Findings prototype and roadmap review](findings-prototype-review.md)
- [superpowers/](superpowers/): specs and implementation plans, one per piece of work.
