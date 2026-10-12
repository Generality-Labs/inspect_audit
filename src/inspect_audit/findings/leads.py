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

from .fs import StoreFS, as_store_fs
from .io import read_current
from .models import Finding, Run
from .render import inputs_lines, sorted_findings
from .review import IssueEntry, Review, apply_review

EXAMPLES = 3

PREAMBLE = (
    "These are hypotheses, not findings. Each was emitted by an automated producer over this "
    "eval's source, dataset or logs, then grouped, with suppressed observations removed by a "
    "reviewer. For each lead, state the suspected mechanism, find the cheapest check that could "
    "refute it, and confirm or retire it. When you record a finding that rests on a lead, cite its "
    "record id (the `<run>/<n>` in backticks) in the evidence. The full observations are in the "
    "run files named below."
)

SKIP_MESSAGE_CHARS = 200


class NoRunsError(ValueError):
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
        raise NoRunsError(f"no runs for {eval} in the current view")
    findings = [f for run in mine for f in run.findings]
    if sample_id is not None:
        findings = [f for f in findings if sample_id in _sample_ids(f)]
    active = [f for f in findings if not f.suppressions]
    suppressed = sum(1 for f in findings if f.suppressions)
    return mine, active, suppressed


def _run_file(run_id: str) -> str:
    return f"runs/{run_id}.run.json"


def _skip_line(producer: str, rule: str, message: str) -> str:
    """One bounded line per skipped check: first line of the reason, or a note that none was recorded."""
    first = message.strip().splitlines()[0].strip() if message.strip() else "(no reason recorded)"
    if len(first) > SKIP_MESSAGE_CHARS:
        first = first[: SKIP_MESSAGE_CHARS - 1] + "…"
    what = "whole producer did not run" if rule == producer else rule
    return f"- {producer} · {what}: {first}"


def _all_skipped(runs: Sequence[Run]) -> bool:
    return all(
        run.outcomes and all(o.status == "skip" for o in run.outcomes) and not run.findings
        for run in runs
    )


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
    parts += ["## Inputs", "", *inputs_lines(runs), ""]

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

    groups: dict[tuple[str, str, str, str], list[Finding]] = {}
    for finding in sorted_findings(findings):
        key = (finding.producer, finding.rule, finding.dimension, finding.severity)
        groups.setdefault(key, []).append(finding)
    shown = {(f.run_id, f.id) for f in findings}
    hidden = (
        sum(
            1
            for run in runs
            for f in run.findings
            if not f.suppressions and (f.run_id, f.id) not in shown
        )
        if sample_id is not None
        else 0
    )
    parts += ["## Leads", ""]
    if _all_skipped(runs):
        parts += ["No producer ran for this eval; see Not examined below.", ""]
    else:
        summary = (
            f"{len(findings)} observation{'' if len(findings) == 1 else 's'} in {len(groups)} "
            f"group(s); {suppressed} suppressed by review and not shown."
        )
        if sample_id is not None:
            summary += (
                f" {hidden} eval-wide observation{'' if hidden == 1 else 's'} (not about this "
                "sample) not shown; see the eval's LEADS.md."
            )
        parts += [summary, ""]
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
        parts += [_skip_line(producer, rule, message) for producer, rule, message in skips]
        parts.append("")
    return "\n".join(parts).rstrip() + "\n"


def leads_markdown(
    store: StoreFS | str | Path, eval: str, review: Review, *, sample_id: str | None = None
) -> str:
    """LEADS.md for one eval from the store's current view, with review applied."""
    runs = apply_review(read_current(as_store_fs(store)), review)
    mine, findings, suppressed = select_leads(runs, eval, sample_id=sample_id)
    return render_leads(eval, mine, findings, suppressed, review.issues, sample_id=sample_id)
