# Vendored skills

The investigator's default loadout is defined in `INVESTIGATION_SKILLS`.
`investigating`, `writing` and `running-jobs` are maintained locally. The references
below derive from upstream skills and are adapted, not verbatim copies.

| Skill | From | Commit |
| --- | --- | --- |
| eval-validity-review | UKGovernmentBEIS/inspect_evals `.claude/skills` | 67a9ea5 (2026-08-26) |
| investigate-dataset | same | 67a9ea5 |
| security-audit-eval | same | 67a9ea5 |
| check-trajectories-workflow | same | 67a9ea5 |
| eval-report-workflow | same | 67a9ea5 |
| read-eval-logs | same | 67a9ea5 |
| view-results | METR hawk `.claude/skills` | 45f629c (2026-08-25) |
| debug-stuck-eval | same | 45f629c |
| babysit-eval | same | 45f629c |

Both source repositories are MIT licensed (Copyright (c) 2024 UK AI Security Institute;
Copyright (c) 2026 METR). `reading-logs`, `analyzing-logs` and `map-inspect-packages`
are mounted too and live in `../../skills` with their own note.

## What was changed

The local adaptations use the configured tools, canonical nine-dimension framework
and one publication contract. Superseded six-section formats, competing security
verdicts and workstation-specific publication workflows have been removed from the
validity, security and evaluation-report references. Investigative methods are retained;
writing and execution policy live in the maintained skills rather than override notes.

Vendored 2026-09-09.

## Default loadout after the September 10 cleanup

The source copies above are retained for provenance and optional use. Four overlapping
workflows are no longer mounted by default: check-trajectories-workflow,
eval-report-workflow, read-eval-logs and babysit-eval. The maintained investigating,
writing and running-jobs skills cover the workflow; reading-logs/analyzing-logs provide
the Inspect APIs. Focused validity, dataset, security and Hawk diagnostic references
remain available. INVESTIGATION_SKILLS in _investigate.py is the authoritative loadout.
