# Assessment record and investigation import

> For agentic workers: executed natively with superpowers:executing-plans; one fresh review per PR.

**Goal:** investigator results reach audits.generality.org: the store gains an Assessment record, and an import turns a published investigation bundle into a run.

**Architecture:** Assessments live on `Run` beside findings, so an imported investigation is one immutable run file and the current view, render and export need no new storage. An assessment names its methodology (`<id>@<version>`), so the store holds GL-format, Epoch-format and future maintainer-methodology assessments without knowing any of them. The import reads bundle JSON directly; the findings package imports nothing from the investigator.

**Spec:** `docs/finding-schema-envelope.md` (Assessment, Assessment measured), `docs/methodology.md`, roadmap milestone 5. Field names follow Laurence's `records.Assessment` on `laurence/epoch-methodology` so his bundles import without loss.

## Global constraints

- YAGNI: no Profile, Sampling or Prose records in the store yet; no Epoch-format reader until that branch lands.
- The findings package stays independent of the investigator modules.
- Raw inputs never leave the importing machine: a bundle's `_inputs/` is not uploaded (same rule as scan samples).
- Additive export change; `SCHEMA` stays 1.

## Review focus

1. An assessment citing a finding that is not in its run: refused, not silently dropped.
2. A taxonomy methodology with a check that is a dimension code (`A`) rather than a contribution (`A.1`): accepted; GL bundles assess both.
3. A methodology the store has never heard of: accepted without validation of its check ids.
4. A measure whose numerator exceeds its denominator, or a zero denominator: refused.
5. Two assessments of the same check under the same methodology in one run: refused.

## PR 1: Assessment record (branch `feat/assessment-record`)

### Task 1: model

- `findings/models.py`: `Measure` (name, numerator ≥ 0, denominator > 0, threshold, source; numerator ≤ denominator; `value` as a plain property) and `Assessment` (methodology, check, level, notes, evidence: locations, findings: fingerprints, measure, impact, fields). `Run.assessments`. Validators: methodology shape; check in the taxonomy (dimension code or contribution id) when the methodology is a known taxonomy; unique (methodology, check) per run; cited fingerprints are the run's own.
- `findings/taxonomy.py`: `assessable_ids()` = dimension codes ∪ contribution ids.
- Regenerate `schema/run.schema.json`.
- Tests in `tests/findings/test_models.py`: round trip; each review-focus line.

### Task 2: export and docs

- `export.py` `_run_entry`: `assessments` with methodology, check, level, notes, impact, measure (plus `value`), findings as record ids, evidence as location keys.
- `docs/finding-schema-envelope.md`: Assessment as built; `docs/findings-cli.md` export section if it lists run entry fields.
- Test in `tests/findings/test_export.py`.

## PR 2: bundle import (branch `feat/import-investigation`, stacked on PR 1)

`inspect-audit-findings import-investigation BUNDLE --eval E [--revision R] [--store S] [--author A]` turns one published GL-format bundle (`published/<id>/`) into one run and writes the lead citations it finds as status decisions.

### Task 3: bundle → run (`findings/investigation.py`)

- Producer `inspect_audit_investigate`; run id `<producer>-<stamp>-<slug>`, stamp from `findings.json`'s modification time (bundles carry no timestamp); `inputs.bundle` = {id, path, sha256 of findings.json}; `inputs.coverage` = coverage counts and denominator; `inputs.limitations` = `audit_limitation` findings, which are not benchmark defects and stay out of the table.
- Revision from `metadata.tex` `\commitref`, else `--revision`, else an error.
- Each other finding: rule = its id, dimension = its section (the sections are `inspect-audit@1` dimension ids), severity lower-cased (null and `None` → `none`), status as written, summary = claim, evidence refs → artifact locations (first primary), lead refs (path ending `findings/LEADS.md` or under `findings/runs/`) → citations, not locations; a finding citing only leads keeps the lead ref as its location. Fingerprint from the shared `fingerprint()` recipe. `source.record` = the bundle's finding.
- Each assessment row → `Assessment(methodology="inspect-audit@1", check=id, level=assessment, notes=result)`; an evidence string naming an imported finding's id → its fingerprint in `findings`, a limitation's id is dropped, any other string → artifact location; `checked_ids` and `affected_ids` → `Measure(name="affected_items")`.
- Returns the run and its citations (record id, citing finding's status, citing record id).
- Tests (`tests/findings/test_investigation.py`, synthetic bundles in tmp_path; no real artefacts committed): a finding's fields; lead refs become citations; a lead-only finding; limitations kept out; assessments with finding ids, artifact evidence and a measure; revision fallback and error; a bundle without assessments or coverage.

### Task 4: the subcommand

- Refuses a bundle already imported (any stored run whose `inputs.bundle.id` matches), naming that run.
- Writes the run with `write_outputs` (which re-renders), then one `set_status` per citation with reason `cited as evidence by <record id>`; a citation whose record is not in the current view is a warning, not a failure.
- Tests in `tests/findings/test_cli.py`: import writes the run and exports its assessments; a cited lead's status and history change; a second import is exit 2; an unresolvable citation warns and still imports.
- Docs: `docs/findings-cli.md` section; `docs/architecture.md` investigations bullet no longer *Planned*; roadmap milestone 5 note.
