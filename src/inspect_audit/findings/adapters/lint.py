"""inspect-evals-lint: every outcome recorded, every diagnostic a finding with the row kept verbatim."""

from __future__ import annotations

import json
import time
from collections.abc import Mapping
from datetime import datetime
from typing import Any, Literal

from ..fingerprint import FINGERPRINT_VERSION, fingerprint
from ..models import (
    CodeLocation,
    Dimension,
    Finding,
    Outcome,
    Run,
    Severity,
    Source,
    Subject,
    utcnow,
)
from . import (
    Context,
    ProducerError,
    new_run_id,
    package_of,
    run_command,
    skip_run,
    subject_for,
)

PRODUCER = "inspect_evals_lint"

# dimension and severity per rule code. Default is ("harness", "minor"); this is the
# table to argue about, and it is one dict.
LINT_RULES: dict[str, tuple[Dimension, Severity]] = {
    "IEBP001": ("grading", "minor"),
    "IEBP002": ("grading", "minor"),
    "IEBP005": ("environment", "minor"),
    "IEBP006": ("environment", "minor"),
    "IEBP007": ("environment", "minor"),
    "IEBP008": ("dataset", "minor"),
    "IEBP009": ("dataset", "minor"),
}
_DEFAULT: tuple[Dimension, Severity] = ("harness", "minor")


def _status(raw: str) -> Literal["pass", "fail", "skip"]:
    """Lint statuses to ours: pass and skip carry over, suppressed is a skip, fail and warn are fails."""
    if raw == "pass":
        return "pass"
    if raw in ("skip", "suppressed"):
        return "skip"
    return "fail"


def parse(
    data: Mapping[str, Any],
    target: str,
    subject: Subject,
    *,
    timestamp: datetime,
    duration_s: float | None = None,
    inputs: Mapping[str, Any] | None = None,
) -> Run:
    """The lint JSON document for `target`'s package, as a Run."""
    run_id = new_run_id(PRODUCER, target, timestamp)
    version = str(data.get("version") or "unknown")
    package = package_of(target)
    packages = [p for p in data.get("packages", []) if p.get("name") == package]
    if not packages:
        found = ", ".join(str(p.get("name")) for p in data.get("packages", [])) or "(none)"
        return Run(
            id=run_id, timestamp=timestamp, producer=PRODUCER, producer_version=version, subject=subject,
            duration_s=duration_s, inputs=dict(inputs or {}),
            outcomes=[Outcome(rule=PRODUCER, status="skip", message=f"lint output has no package {package!r}; found {found}")],
        )
    entry = packages[0]
    outcomes = [
        Outcome(
            rule=str(row.get("code") or row.get("rule")),
            status=_status(str(row.get("status"))),
            message=row.get("message"),
        )
        for row in entry.get("outcomes", [])
    ]
    findings: list[Finding] = []
    for row in entry.get("diagnostics", []):
        code = str(row.get("code") or row.get("rule"))
        dimension, severity = LINT_RULES.get(code, _DEFAULT)
        primary = CodeLocation(
            role="primary", file=str(row.get("file")), line=row.get("line"), end_line=row.get("end_line"), column=row.get("column")
        )
        findings.append(
            Finding(
                fingerprint=fingerprint(PRODUCER, code, target, primary),
                fingerprint_version=FINGERPRINT_VERSION,
                producer=PRODUCER,
                rule=code,
                subject=subject,
                dimension=dimension,
                severity=severity,
                status="supported",
                summary=str(row.get("message") or code),
                locations=[primary],
                run_id=run_id,
                source=Source(format=f"inspect_evals_lint.Diagnostic@{version}", record=dict(row)),
            )
        )
    return Run(
        id=run_id, timestamp=timestamp, producer=PRODUCER, producer_version=version, subject=subject,
        duration_s=duration_s, inputs=dict(inputs or {}), outcomes=outcomes, findings=findings,
    )


def run(target: str, ctx: Context) -> Run:
    """Invoke lint for `target`'s package and parse it. Exit 1 means findings; anything else is a skip."""
    timestamp = utcnow()
    argv = [*ctx.producers.lint, "--root", str(ctx.ie_root), package_of(target), "--output-format", "json"]
    started = time.monotonic()
    try:
        result = run_command(argv, timeout=ctx.producers.timeout_s)
    except ProducerError as ex:
        return skip_run(PRODUCER, target, ctx, str(ex), timestamp=timestamp)
    duration = time.monotonic() - started
    if result.returncode not in (0, 1):
        return skip_run(PRODUCER, target, ctx, f"lint exit {result.returncode}: {(result.stderr or result.stdout)[-1500:]}", timestamp=timestamp)
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError as ex:
        return skip_run(PRODUCER, target, ctx, f"lint output is not JSON: {ex}; stderr: {result.stderr[-500:]}", timestamp=timestamp)
    try:
        return parse(
            data, target, subject_for(target, ctx), timestamp=timestamp, duration_s=duration, inputs={"argv": argv}
        )
    except Exception as ex:  # a producer whose output we cannot read is a skipped producer, not a dead sweep
        return skip_run(PRODUCER, target, ctx, f"could not parse {PRODUCER} output: {type(ex).__name__}: {ex}", timestamp=timestamp)
