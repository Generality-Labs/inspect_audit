"""One eval's reviewed findings as leads: hypotheses with a location attached, for an agent to check.

The investigator and the sample auditor already treat mechanical diffs and worker verdicts as
leads rather than conclusions. This renders the deterministic producers' output the same way:
grouped, capped, with suppressions applied and record ids to cite, so a lint warning and the
agent's verdict on it end up as one record's story.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from pathlib import Path

from .io import read_current
from .models import Finding, Run
from .render import _inputs_lines, _sorted_findings
from .review import IssueEntry, Review, apply_review

EXAMPLES = 3

PREAMBLE = (
    "These are hypotheses, not findings. Each was emitted by a deterministic producer over this "
    "eval's source, dataset or logs, then grouped, with suppressed observations removed by a "
    "reviewer. Treat each lead as you treat a worker verdict: state the suspected mechanism, find "
    "the cheapest check that could refute it, and confirm or retire it. When you record a finding "
    "that rests on a lead, cite its record id (the `<run>/<n>` in backticks) in the evidence. The "
    "full observations are in the run files named below."
)


class NoRuns(ValueError):
    """The current view has no runs for the eval."""


def _sample_ids(finding: Finding) -> set[str]:
    ids: set[str] = set()
    for location in finding.locations:
        sample_id = getattr(location, "sample_id", None)
        if sample_id is not None:
            ids.add(str(sample_id))
    return ids


def select_leads(
    runs: Sequence[Run], eval: str, *, sample_id: str | None = None
) -> tuple[list[Run], list[Finding], int]:
    """The eval's runs, its active findings (for one sample when asked), and how many were suppressed."""
    mine = [run for run in runs if run.subject.eval == eval]
    if not mine:
        raise NoRuns(f"no runs for {eval} in the current view")
    active = [f for run in mine for f in run.findings if not f.suppressions]
    suppressed = sum(1 for run in mine for f in run.findings if f.suppressions)
    if sample_id is not None:
        active = [f for f in active if sample_id in _sample_ids(f)]
    return mine, active, suppressed


def _run_file(run_id: str) -> str:
    return f"runs/{run_id}.run.json"


def render_leads(
    eval: str,
    runs: Sequence[Run],
    findings: Sequence[Finding],
    suppressed: int,
    issues: Sequence[IssueEntry],
    *,
    sample_id: str | None = None,
) -> str:
    """LEADS.md: preamble, inputs, accepted issues, grouped and capped leads, skipped checks."""
    scope = f" (sample {sample_id})" if sample_id is not None else ""
    parts: list[str] = [f"# Leads for {eval}{scope}", "", PREAMBLE, ""]
    parts += ["## Inputs", "", *_inputs_lines(runs), ""]

    relevant = [issue for issue in issues if issue.subject == eval]
    if relevant:
        linked = Counter(f.issue for run in runs for f in run.findings if f.issue)
        parts += ["## Already accepted as issues", ""]
        for issue in relevant:
            k = linked.get(issue.id, 0)
            line = f"- {issue.id} · {issue.title} · {k} current observation{'' if k == 1 else 's'}"
            if issue.github:
                line += f" · {issue.github}"
            parts.append(line)
        parts.append("")

    group_count = len({(f.producer, f.rule) for f in findings})
    parts += [
        "## Leads",
        "",
        f"{len(findings)} observation{'' if len(findings) == 1 else 's'} in {group_count} "
        f"group(s); {suppressed} suppressed by review and not shown.",
        "",
    ]
    groups: dict[tuple[str, str, str, str], list[Finding]] = {}
    for finding in _sorted_findings(findings):
        key = (finding.producer, finding.rule, finding.dimension, finding.severity)
        groups.setdefault(key, []).append(finding)
    for (producer, rule, dimension, severity), members in groups.items():
        n = len(members)
        parts.append(
            f"### {producer} · {rule} · {dimension} · {severity} · {n} observation{'' if n == 1 else 's'}"
        )
        parts.append("")
        for finding in members[:EXAMPLES]:
            tag = f" (issue {finding.issue})" if finding.issue else ""
            parts.append(
                f"- `{finding.id}` · `{finding.primary_location.key()}` · {finding.summary}{tag}"
            )
        if n > EXAMPLES:
            files = ", ".join(f"`{_run_file(r)}`" for r in sorted({f.run_id for f in members}))
            parts.append(f"- and {n - EXAMPLES} more in {files}")
        parts.append("")

    skips = [
        (run.producer, outcome.rule, outcome.message or "")
        for run in runs
        for outcome in run.outcomes
        if outcome.status == "skip"
    ]
    if skips:
        parts += [
            "## Not examined",
            "",
            "Checks that did not run; absence of a lead here is not evidence.",
            "",
        ]
        parts += [f"- {producer} · {rule}: {message}" for producer, rule, message in skips]
        parts.append("")
    return "\n".join(parts).rstrip() + "\n"


def leads_markdown(out: Path, eval: str, review: Review, *, sample_id: str | None = None) -> str:
    """LEADS.md for one eval from the current view under `out`, with review applied."""
    runs = apply_review(read_current(out), review)
    mine, findings, suppressed = select_leads(runs, eval, sample_id=sample_id)
    return render_leads(eval, mine, findings, suppressed, review.issues, sample_id=sample_id)
