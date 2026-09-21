"""Validate framework assessments and derive accessible report tables."""

import html
import json
import re
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from ._coverage import coverage_summary


class CheckAssessment(BaseModel):
    """A framework judgment with optional question-level counts."""

    model_config = ConfigDict(extra="forbid")
    id: str
    assessment: Literal["None", "Minor", "Major", "Critical", "Not assessed", "Not applicable"]
    result: str = Field(min_length=1)
    evidence: list[str]
    checked_ids: list[str] | None = None
    affected_ids: list[str] | None = None


def framework_checks(report: Path) -> dict[str, str]:
    """Read check names from the pinned framework rather than maintain a second registry."""
    source = (report / "framework/auditframework.sty").read_text()
    source = re.sub(r"(?m)^\s*%.*$", "", source)
    letters = dict(re.findall(r"\\dimensionletter\{([^}]+)\}\{([^}]+)\}", source))
    checks: dict[str, str] = {}
    for block in re.split(r"\\definedimension\{", source)[1:]:
        match = re.match(r"([^}]+)\}\{([^}]+)\}", block)
        if match is None or match[1] not in letters:
            continue
        letter = letters[match[1]]
        checks[letter] = match[2]
        for index, name in enumerate(re.findall(r"\\gsub\{([^}]+)\}", block), 1):
            checks[f"{letter}.{index}"] = name.replace(r"\&", "&")
    if not checks:
        raise ValueError("No assessment checks found in the supplied framework")
    return checks


def prepare_assessments(report: Path) -> None:
    """Stage a checklist and schema for a new investigation."""
    (report / "framework/checks.json").write_text(json.dumps(framework_checks(report), indent=2))
    (report / "assessments.schema.json").write_text(json.dumps(TypeAdapter(list[CheckAssessment]).json_schema(), indent=2))
    (report / "assessments.json").write_text("[]\n")
    (report / "audit-tables.html").write_text("<!-- Tables generated at publication. -->\n")


def assessment_tables(report: Path) -> str:
    """Validate complete assessments and coverage totals; return tables from those records."""
    checks = framework_checks(report)
    rows = TypeAdapter(list[CheckAssessment]).validate_json((report / "assessments.json").read_text())
    ids = [row.id for row in rows]
    if len(ids) != len(set(ids)) or set(ids) != set(checks):
        raise ValueError("assessments.json must contain every framework dimension and check exactly once")
    coverage = None
    if (report / "coverage.json").exists():
        recorded = json.loads((report / "coverage.json").read_text())
        coverage = coverage_summary([r["question_id"] for r in recorded["questions"]],
                                    [a for r in recorded["questions"] for a in r["assessments"]])
        if recorded != coverage:
            raise ValueError("coverage.json must match export_coverage results; resolve source assessments before exporting")
    population = {r["question_id"] for r in coverage["questions"]} if coverage else None
    for row in rows:
        if not row.result.strip() or any(not ref.strip() for ref in row.evidence):
            raise ValueError(f"{row.id}: empty result or evidence")
        if row.assessment not in ("Not assessed", "Not applicable") and not row.evidence:
            raise ValueError(f"{row.id}: completed assessments require evidence")
        if (row.checked_ids is None) != (row.affected_ids is None):
            raise ValueError(f"{row.id}: provide both checked_ids and affected_ids or neither")
        if row.checked_ids is not None and row.affected_ids is not None:
            if population is None:
                raise ValueError(f"{row.id}: question counts require report/coverage.json")
            if len(set(row.checked_ids)) != len(row.checked_ids) or len(set(row.affected_ids)) != len(row.affected_ids):
                raise ValueError(f"{row.id}: duplicate question IDs")
            if row.assessment == "None" and row.affected_ids:
                raise ValueError(f"{row.id}: a clean assessment cannot list affected questions")
            if not set(row.affected_ids) <= set(row.checked_ids) <= population:
                raise ValueError(f"{row.id}: affected IDs must be checked and in the population")
    esc = html.escape
    parts = []
    if coverage:
        parts.append('<h3>Questions assessed</h3><table><tr><th>Status</th><th>Questions</th><th>Percent</th></tr>')
        for status, count in coverage["counts"].items():
            parts.append(f'<tr><td>{esc(status.replace("_", " ").capitalize())}</td><td>{count}/{coverage["denominator"]}</td><td>{coverage["percentages"][status]:.1f}%</td></tr>')
        parts.append('</table><h3>Defect types</h3><p>Counts are unique questions per type; types may overlap. These are not counts of misgraded submissions.</p><table><tr><th>Type</th><th>Questions</th></tr>')
        for kind, count in coverage["defect_counts"].items():
            parts.append(f'<tr><td>{esc(kind.replace("_", " "))}</td><td>{count}/{coverage["denominator"]}</td></tr>')
        parts.append(f'<tr><td>Unclassified defects</td><td>{coverage["unclassified_defects"]}</td></tr></table>')
    by_id = {row.id: row for row in rows}
    for dimensions in (True, False):
        parts.append('<h3>Scorecard</h3>' if dimensions else '<details><summary>Full check assessments</summary>')
        parts.append('<table><tr><th>Check</th><th>Assessment</th><th>Affected / checked</th><th>Result</th><th>Evidence</th></tr>')
        for key, name in checks.items():
            if ('.' not in key) != dimensions:
                continue
            row = by_id[key]
            count = f"{len(row.affected_ids or [])}/{len(row.checked_ids)}" if row.checked_ids is not None else "—"
            badge = row.assessment.lower().replace(' ', '-')
            parts.append(f'<tr><td>{esc(name)}</td><td><span class="assessment {badge}">{esc(row.assessment)}</span></td><td>{count}</td><td>{esc(row.result)}</td><td>{esc("; ".join(row.evidence))}</td></tr>')
        parts.append('</table>' if dimensions else '</table></details>')
    if coverage:
        parts.append('<details><summary>Question-level results</summary><table><tr><th>Question</th><th>Status</th><th>Defect types</th><th>Explanation</th><th>Evidence</th><th>Next check</th></tr>')
        for question in coverage["questions"]:
            assessments = question["assessments"]
            kinds = sorted({kind for a in assessments for kind in a["defect_types"]})
            explanations = " ".join(a["explanation"] for a in assessments)
            evidence = "; ".join(dict.fromkeys(ref for a in assessments for ref in a["evidence"]))
            next_checks = " ".join(a["next_check"] for a in assessments if a["next_check"])
            parts.append(f'<tr><td>{esc(question["question_id"])}</td><td>{esc(question["status"])}</td><td>{esc(", ".join(kinds))}</td><td>{esc(explanations)}</td><td>{esc(evidence)}</td><td>{esc(next_checks)}</td></tr>')
        parts.append('</table></details>')
    return '\n'.join(parts)
