# Vendored skills

The investigator's default loadout is defined in `INVESTIGATION_SKILLS`.
`investigating` and `running-jobs` are maintained locally. The references
below derive from upstream skills and are adapted, not verbatim copies.

| Skill | From | Commit |
| --- | --- | --- |
| eval-validity-review | UKGovernmentBEIS/inspect_evals `.claude/skills` | 67a9ea5 (2026-08-26) |
| investigate-dataset | same | 67a9ea5 |
| security-audit-eval | same | 67a9ea5 |
| check-trajectories-workflow | same | 67a9ea5 |
| read-eval-logs | same | 67a9ea5 |

All source repositories are MIT licensed (Copyright (c) 2024 UK AI Security Institute;
Meridian Labs, see ../../skills/LICENSE.meridian). `reading-logs`, `analyzing-logs` and `map-inspect-packages`
are mounted too and live in `../../skills` with their own note.

## What was changed

The local adaptations use the configured tools, canonical nine-dimension framework
and one publication contract. Superseded six-section formats, competing security
verdicts and workstation-specific publication workflows have been removed from the
validity and security references. Investigative methods are retained;
findings and execution policy live in the maintained skills rather than override notes.

Vendored 2026-09-09.

## Default loadout

INVESTIGATION_SKILLS in _investigate.py is the authoritative loadout. The maintained
investigating and running-jobs skills define the investigation workflow. Publishing
skills are supplied by the chosen publisher only after findings submission.
The old writing and eval-report-workflow skills have been removed.

## Removed 2026-10-20

view-results, debug-stuck-eval, babysit-eval (METR hawk) and babysitting-evals (Meridian)
mapped Hawk and `inspect ctl` diagnostics onto `jobs()` actions (watch, trace,
stacktrace, samples, transcripts) that the investigator no longer has: jobs now run
through one small interface on Hawk or locally, and a job's runner log is the
diagnostic both places share.
