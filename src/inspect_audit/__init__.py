"""Benchmark validity auditing for Inspect AI evals."""

from ._audit import audit_task
from ._candidates import attempts, candidates
from ._item import AttemptRef, AuditItem
from ._resolve import resolve_task, resolve_task_from_log
from ._sandbox import audit_sandbox, task_requirements

__version__ = "0.0.1"

__all__ = [
    # audit
    "audit_task",
    # selection
    "attempts",
    "candidates",
    # items
    "AttemptRef",
    "AuditItem",
    # tasks
    "resolve_task",
    "resolve_task_from_log",
    # sandbox
    "audit_sandbox",
    "task_requirements",
    # version
    "__version__",
]
