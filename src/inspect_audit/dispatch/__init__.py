"""Dispatch: where jobs run, who they run as, what they may do, and what they cost.

- `jobs`: a job, its lifecycle states, the `Runs` interface.
- `local`: jobs on this machine, as Inspect's detached eval sets.
- `hawk`: everything that only exists on Hawk (login, submit, status, logs,
  `hawk:` log fetching, key delivery for the launcher).
- `policy`: the frozen launch policy and the authoriser every child job passes.
- `packages`: the pip specs a fresh runner installs.
- `spend`: what the billed keys have left.
- `launcher`, `openrouter`, `secrets`, `cli`: a capped OpenRouter key per
  audit, always revoked (`inspect-audit-launch`).

Nothing here imports the agents that use it.
"""

from .hawk import HawkLogin, HawkRuns, hawk_config, hawk_supports_restricted
from .jobs import (
    COMPLETE,
    FAILED,
    FINISHED,
    RUNNING,
    STOPPED,
    STUCK,
    Job,
    Runs,
    validate_environment,
)
from .local import JOB_ENVIRONMENT, LocalRuns

__all__ = [
    "COMPLETE",
    "FAILED",
    "FINISHED",
    "JOB_ENVIRONMENT",
    "RUNNING",
    "STOPPED",
    "STUCK",
    "HawkLogin",
    "HawkRuns",
    "Job",
    "LocalRuns",
    "Runs",
    "hawk_config",
    "hawk_supports_restricted",
    "validate_environment",
]
