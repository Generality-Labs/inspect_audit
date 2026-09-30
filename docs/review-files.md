# Review files

Producer run files are never edited. A person's decisions live in two YAML files beside the output, are applied when summaries and parquet are rendered, and survive every rerun. Pass `--review DIR` to `run` or `summary`; the default is the `--out` directory.

## suppressions.yaml

A list. Each entry rules out every observation of one rule, on one eval or all of them, optionally for one producer. Suppressed observations stay in the run files and the parquet with `suppressed` true; they leave the Findings section and the counts and appear under Suppressed with the reason.

```yaml
- rule: answer_length
  subject: inspect_evals/stereoset     # or "*" for every eval
  producer: inspect_dataset            # optional
  kind: false_positive                 # free text; false_positive, accepted_risk, duplicate
  author: matt
  reason: StereoSet answers are structs; the scanner measures their repr
  since: 2026-09-30
```

## issues.yaml

A list. Each entry is a problem a person has accepted. It links the observations that evidence it by fingerprint, so a rerun that observes the same thing keeps the link. A fingerprint may belong to one issue only.

```yaml
- id: ISS-0001
  title: strong_reject records 313 samples where eval.yaml declares 324
  subject: inspect_evals/strong_reject
  findings: [sha256:3f9c…]             # fingerprints from findings.parquet or the run files
  author: matt
  opened: 2026-09-30
  reason: confirmed against the dataset on HuggingFace
  github: https://github.com/UKGovernmentBEIS/inspect_evals/issues/0000   # once filed
```

The Issues section of an eval's summary lists each issue with the number of current observations linked to it. An issue whose fingerprints no longer appear is shown with "no current observation" and a warning is printed; that is a prompt to re-check, not evidence of a fix.

Fingerprints are in `findings.parquet` (`fingerprint` column) and in each run file. Record ids (`<run id>/<n>`) address one observation in one run; fingerprints match observations across runs.
