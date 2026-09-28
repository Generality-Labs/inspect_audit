# Vendored skills

The investigator's default loadout is defined in `INVESTIGATION_SKILLS`.
`investigating`, `writing` and `running-jobs` are maintained locally. The references
below derive from upstream skills and are adapted, not verbatim copies.

| Skill | From | Commit |
| --- | --- | --- |
| eval-validity-review | UKGovernmentBEIS/inspect_evals `.claude/skills` | 67a9ea5 (2026-08-26) |
| investigate-dataset | same | 67a9ea5 |
| security-audit-eval | same | 67a9ea5 |
| debug-stuck-eval | METR hawk `.claude/skills` | 45f629c (2026-08-25) |

Both source repositories are MIT licensed (Copyright (c) 2024 UK AI Security Institute;
Copyright (c) 2026 METR). `reading-logs` and `analyzing-logs`
are mounted too and live in `../../skills` with their own note.

## What was changed

The local adaptations use the configured tools, canonical nine-dimension framework
and one publication contract. Superseded six-section formats, competing security
verdicts and workstation-specific publication workflows have been removed from the
validity, security and evaluation-report references. Investigative methods are retained;
writing and execution policy live in the maintained skills rather than override notes.

Vendored 2026-09-09.

## Removed 2026-09-28

check-trajectories-workflow, eval-report-workflow, read-eval-logs, babysit-eval (unmounted
since 10 Sep) and view-results (its in-container note is now in running-jobs) were deleted
from the package; they remain in git history and upstream. INVESTIGATION_SKILLS in
_investigate.py is the authoritative loadout.
