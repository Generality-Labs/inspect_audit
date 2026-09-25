"""Markdown summaries from runs, by string templating. No model calls."""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence

from .models import Finding, Run

NOISE_THRESHOLD = 100

_SEVERITY_ORDER = {"critical": 0, "major": 1, "minor": 2, "none": 3}


def _table(headers: Sequence[str], rows: Sequence[Sequence[str]]) -> str:
    head = "| " + " | ".join(headers) + " |"
    rule = "|" + "---|" * len(headers)
    body = ["| " + " | ".join(row) + " |" for row in rows]
    return "\n".join([head, rule, *body])


def _sorted_findings(findings: Sequence[Finding]) -> list[Finding]:
    return sorted(findings, key=lambda f: (_SEVERITY_ORDER[f.severity], f.rule, f.primary_location.key()))


def _finding_line(finding: Finding) -> str:
    return (
        f"- {finding.severity} · {finding.dimension} · {finding.rule} · "
        f"`{finding.primary_location.key()}` · {finding.summary}"
    )


def _is_skipped(run: Run) -> bool:
    return bool(run.outcomes) and all(outcome.status == "skip" for outcome in run.outcomes)


def render_eval_summary(runs: Sequence[Run]) -> str:
    """One eval: subject, outcomes across producers, counts, then every finding by producer."""
    runs = sorted(runs, key=lambda r: (r.producer, r.id))
    subject = runs[0].subject
    parts: list[str] = [f"# {subject.eval}", ""]

    dataset = subject.dataset
    subject_rows = [
        ["Revision", subject.revision.commit or "", subject.revision.package_version or ""],
        ["Task version", subject.task_version.full if subject.task_version else "", ""],
        ["Dataset", (dataset.path if dataset and dataset.path else ""), (dataset.revision if dataset and dataset.revision else "")],
    ]
    parts += [_table(["Subject", "", ""], subject_rows), ""]

    skipped = [run.producer for run in runs if _is_skipped(run)]
    if skipped:
        parts += [", ".join(f"{producer}: skipped" for producer in skipped), ""]

    outcome_rows = [
        [run.producer, outcome.rule, outcome.status, outcome.message or ""]
        for run in runs
        for outcome in run.outcomes
        if outcome.status != "pass"
    ]
    passes = sum(1 for run in runs for outcome in run.outcomes if outcome.status == "pass")
    parts += ["## Outcomes", "", f"{passes} passing outcome(s) not listed.", ""]
    if outcome_rows:
        parts += [_table(["Producer", "Rule", "Status", "Message"], outcome_rows), ""]

    by_producer = Counter(finding.producer for run in runs for finding in run.findings)
    parts += [
        "## Findings by producer",
        "",
        _table(["Producer", "Findings"], [[producer, str(n)] for producer, n in sorted(by_producer.items())]),
        "",
    ]
    by_dimension = Counter((finding.dimension, finding.severity) for run in runs for finding in run.findings)
    dimension_rows = [
        [dimension, severity, str(n)]
        for (dimension, severity), n in sorted(by_dimension.items(), key=lambda kv: (kv[0][0], _SEVERITY_ORDER[kv[0][1]]))
    ]
    parts += ["## Findings by dimension and severity", "", _table(["Dimension", "Severity", "Findings"], dimension_rows), ""]

    by_rule = Counter((finding.producer, finding.rule) for run in runs for finding in run.findings)
    noisy = [(producer, rule, n) for (producer, rule), n in sorted(by_rule.items()) if n > NOISE_THRESHOLD]
    if noisy:
        parts += ["## Noise", ""]
        parts += [f"- {producer}: {rule} produced {n} findings; likely a rule that does not fit this eval." for producer, rule, n in noisy]
        parts.append("")

    parts += ["## Findings", ""]
    for run in runs:
        parts += [f"### {run.producer} ({run.id})", ""]
        if not run.findings:
            parts += ["No findings.", ""]
            continue
        parts += [_finding_line(finding) for finding in _sorted_findings(run.findings)]
        parts.append("")
    return "\n".join(parts).rstrip() + "\n"


def render_sweep_summary(runs_by_eval: Mapping[str, Sequence[Run]]) -> str:
    """Every eval on one line: per-producer finding counts, skips called out."""
    rows: list[list[str]] = []
    for eval_name in sorted(runs_by_eval):
        cells: list[str] = []
        for run in sorted(runs_by_eval[eval_name], key=lambda r: r.producer):
            if _is_skipped(run):
                cells.append(f"{run.producer}: skipped")
            else:
                n = len(run.findings)
                cells.append(f"{run.producer}: {n} finding{'' if n == 1 else 's'}")
        rows.append([eval_name, "; ".join(cells)])
    return "\n".join(["# Sweep summary", "", _table(["Eval", "Producers"], rows), ""])
