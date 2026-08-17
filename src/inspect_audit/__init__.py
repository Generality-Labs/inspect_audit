"""Benchmark validity auditing for Inspect AI evals."""

from ._audit import audit_task
from ._candidates import attempts, candidates, input_hash
from ._case import AttemptRef, CaseSpec
from ._resolve import resolve_task, resolve_task_from_log, task_ref
from ._sandbox import audit_sandbox, task_requirements

__version__ = "0.0.1"

__all__ = [
    "AttemptRef",
    "CaseSpec",
    "__version__",
    "attempts",
    "audit_sandbox",
    "audit_task",
    "candidates",
    "input_hash",
    "resolve_task",
    "resolve_task_from_log",
    "task_ref",
    "task_requirements",
]
