"""Markdown summaries from runs, by string templating. No model calls."""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from typing import Any

from .models import Finding, Run
from .review import IssueEntry

NOISE_THRESHOLD = 100
EXAMPLES = 2  # observations shown under a grouped rule

_SEVERITY_ORDER = {"critical": 0, "major": 1, "minor": 2, "none": 3}


def _table(headers: Sequence[str], rows: Sequence[Sequence[str]]) -> str:
    head = "| " + " | ".join(headers) + " |"
    rule = "|" + "---|" * len(headers)
    body = ["| " + " | ".join(row) + " |" for row in rows]
    return "\n".join([head, rule, *body])


def sorted_findings(findings: Sequence[Finding]) -> list[Finding]:
    return sorted(
        findings, key=lambda f: (_SEVERITY_ORDER[f.severity], f.rule, f.primary_location.key())
    )


def _finding_line(finding: Finding) -> str:
    return (
        f"- {finding.severity} · {finding.dimension} · {finding.rule} · "
        f"`{finding.primary_location.key()}` · {finding.summary}"
    )


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def inputs_lines(runs: Sequence[Run]) -> list[str]:
    """What the producers examined, and what they left out, as bullet lines."""
    lines: list[str] = []
    dataset = next(
        (_dict(run.inputs.get("dataset")) for run in runs if "dataset" in run.inputs), {}
    )
    if dataset:
        where = (
            "through the task's own loader"
            if dataset.get("mode") == "task"
            else ", ".join(
                f"{key} {dataset[key]}"
                for key in ("config", "split", "revision")
                if dataset.get(key)
            )
        )
        origin = (
            "declared in the pilot config" if dataset.get("declared") else "inferred from eval.yaml"
        )
        lines.append(
            f"- Dataset scanned: `{dataset.get('path')}`{f' ({where})' if where else ''}, {origin}."
        )
        if dataset.get("mode") == "task":
            scorers = dataset.get("scorers") or []
            lines.append(
                f"- Scorers the scan assumed: {', '.join(f'`{s}`' for s in scorers)}."
                if scorers
                else "- Scorers the scan assumed: none; the task declares no registered scorer."
            )
    else:
        lines.append("- Dataset: no dataset scan ran.")
    logs = next((_dict(run.inputs.get("logs")) for run in runs if "logs" in run.inputs), {})
    if logs:
        used = _list(logs.get("used"))
        excluded = _list(logs.get("excluded"))
        count_excluded = _list(logs.get("count_excluded"))
        lines.append(
            f"- Logs: {len(used)} log(s) used, {len(excluded)} excluded, "
            f"{len(count_excluded)} not compared for sample count."
        )
        for entry in excluded:
            lines.append(f"  - excluded `{_dict(entry).get('path')}`: {_dict(entry).get('reason')}")
        for entry in count_excluded:
            lines.append(
                f"  - not compared `{_dict(entry).get('path')}`: {_dict(entry).get('reason')}"
            )
    else:
        lines.append("- Logs: no logs examined.")
    comparison = next(
        (_dict(run.inputs.get("comparison")) for run in runs if "comparison" in run.inputs), {}
    )
    if comparison:
        lines.append(
            f"- Header checks compared against "
            f"{comparison.get('commit') or comparison.get('package_version')}, "
            f"task version {comparison.get('task_version')}."
        )
    return lines


def _active(run: Run) -> list[Finding]:
    return [finding for finding in run.findings if not finding.suppressions]


def _suppressed(run: Run) -> list[Finding]:
    return [finding for finding in run.findings if finding.suppressions]


def _example_line(finding: Finding) -> str:
    return f"  - `{finding.primary_location.key()}` · {finding.summary}"


def _grouped_lines(findings: Sequence[Finding]) -> list[str]:
    """One line per (severity, dimension, rule); a group of more than one shows a count and examples."""
    groups: dict[tuple[str, str, str], list[Finding]] = {}
    for finding in sorted_findings(findings):
        groups.setdefault((finding.severity, finding.dimension, finding.rule), []).append(finding)
    lines: list[str] = []
    for (severity, dimension, rule), members in groups.items():
        if len(members) == 1:
            lines.append(_finding_line(members[0]))
            continue
        lines.append(f"- {severity} · {dimension} · {rule} · {len(members)} observations")
        lines += [_example_line(member) for member in members[:EXAMPLES]]
    return lines


def _is_skipped(run: Run) -> bool:
    return bool(run.outcomes) and all(outcome.status == "skip" for outcome in run.outcomes)


def render_eval_summary(runs: Sequence[Run], issues: Sequence[IssueEntry] = ()) -> str:
    """One eval: subject, inputs, outcomes, counts, findings grouped by rule, suppressions, issues."""
    runs = sorted(runs, key=lambda r: (r.producer, r.id))
    subject = runs[0].subject
    parts: list[str] = [f"# {subject.eval}", ""]

    dataset = subject.dataset
    subject_rows = [
        ["Revision", subject.revision.commit or "", subject.revision.package_version or ""],
        ["Task version", subject.task_version.full if subject.task_version else "", ""],
        [
            "Dataset",
            (dataset.path if dataset and dataset.path else ""),
            (dataset.revision if dataset and dataset.revision else ""),
        ],
    ]
    parts += [_table(["Subject", "", ""], subject_rows), ""]

    skipped = [run.producer for run in runs if _is_skipped(run)]
    if skipped:
        parts += [", ".join(f"{producer}: skipped" for producer in skipped), ""]

    parts += ["## Inputs", "", *inputs_lines(runs), ""]

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

    active_count: Counter[str] = Counter()
    suppressed_count: Counter[str] = Counter()
    for run in runs:
        active_count[run.producer] += len(_active(run))
        suppressed_count[run.producer] += len(_suppressed(run))
    parts += [
        "## Findings by producer",
        "",
        _table(
            ["Producer", "Findings", "Suppressed"],
            [
                [producer, str(active_count[producer]), str(suppressed_count[producer])]
                for producer in sorted({run.producer for run in runs})
            ],
        ),
        "",
    ]
    by_dimension = Counter(
        (finding.dimension, finding.severity) for run in runs for finding in _active(run)
    )
    dimension_rows = [
        [dimension, severity, str(n)]
        for (dimension, severity), n in sorted(
            by_dimension.items(), key=lambda kv: (kv[0][0], _SEVERITY_ORDER[kv[0][1]])
        )
    ]
    parts += [
        "## Findings by dimension and severity",
        "",
        _table(["Dimension", "Severity", "Findings"], dimension_rows),
        "",
    ]

    by_rule = Counter((finding.producer, finding.rule) for run in runs for finding in _active(run))
    noisy = [
        (producer, rule, n)
        for (producer, rule), n in sorted(by_rule.items())
        if n > NOISE_THRESHOLD
    ]
    if noisy:
        parts += ["## Noise", ""]
        parts += [
            f"- {producer}: {rule} produced {n} findings; likely a rule that does not fit this eval."
            for producer, rule, n in noisy
        ]
        parts.append("")

    parts += ["## Findings", ""]
    for run in runs:
        parts += [f"### {run.producer} ({run.id})", ""]
        active = _active(run)
        if not active:
            parts += ["No findings.", ""]
            continue
        parts += _grouped_lines(active)
        parts.append("")

    suppressed_groups = Counter(
        (finding.producer, finding.rule, s.kind, s.provenance.author, s.provenance.reason or "")
        for run in runs
        for finding in _suppressed(run)
        for s in finding.suppressions[:1]
    )
    if suppressed_groups:
        parts += ["## Suppressed", ""]
        for (producer, rule, kind, author, reason), n in sorted(suppressed_groups.items()):
            parts.append(
                f"- {producer} · {rule} · {n} observation{'' if n == 1 else 's'} · {kind} · {author}: {reason}"
            )
        parts.append("")

    relevant = [issue for issue in issues if issue.subject == subject.eval]
    if relevant:
        linked = Counter(finding.issue for run in runs for finding in run.findings if finding.issue)
        parts += ["## Issues", ""]
        for issue in relevant:
            k = linked.get(issue.id, 0)
            line = f"- {issue.id} · {issue.title} · {k} current observation{'' if k == 1 else 's'}"
            if issue.github:
                line += f" · {issue.github}"
            if k == 0:
                line += " · no current observation"
            parts.append(line)
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
                n, m = len(_active(run)), len(_suppressed(run))
                cell = f"{run.producer}: {n} finding{'' if n == 1 else 's'}"
                if m:
                    cell += f", {m} suppressed"
                logs = _dict(run.inputs.get("logs"))
                if logs:
                    used = len(_list(logs.get("used")))
                    excluded = len(_list(logs.get("excluded")))
                    cell += f" ({used} log{'' if used == 1 else 's'}, {excluded} excluded)"
                cells.append(cell)
        rows.append([eval_name, "; ".join(cells)])
    return "\n".join(["# Sweep summary", "", _table(["Eval", "Producers"], rows), ""])
