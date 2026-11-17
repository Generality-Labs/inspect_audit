"""Epoch AI's benchmark review as an Inspect Auditor package: their rubric, their page.

`package: epoch` in an audit config. A skeleton: the rubric and its verdict rule are
Epoch's, the page is a plain rendering of the rows until Epoch's own form is ported.
"""

import json
from pathlib import Path

from inspect_audit.package import AuditPackage, Findings, Publisher, read_bundle

from .rubric import rows, verdict

HERE = Path(__file__).parent


async def build(root: Path, bundle: Path) -> Path:
    """The review page from the frozen findings: verdict, then every row as rated."""
    findings, _ = read_bundle(bundle)
    out = root / "epoch-page"
    out.mkdir(exist_ok=True)
    (out / "review.md").write_text(page(findings))
    result, reasons = verdict(findings.auditor.ratings)
    (out / "verdict.json").write_text(json.dumps({"verdict": result, "reasons": reasons}, indent=2))
    return out


def page(findings: Findings) -> str:
    result, reasons = verdict(findings.auditor.ratings)
    ratings = findings.auditor.ratings
    lines = [f"# {result}", "", *[f"- {r}" for r in reasons], "", findings.auditor.summary, ""]
    section = None
    for row in rows():
        if row["section"] != section:
            section = row["section"]
            lines += ["", f"## {section}", "", "| Check | Status | Notes |", "|---|---|---|"]
        rating = ratings.get(row["id"])
        status = rating.label if rating else "Not Reviewed"
        note = (rating.explanation if rating else "").replace("|", "/").replace("\n", " ")
        lines.append(f"| {row['name']} | {status} | {note} |")
    if findings.auditor.findings:
        lines += ["", "## Errors", ""]
        lines += [
            f"- **{f.issue}** ({f.criterion}): {f.explanation}" for f in findings.auditor.findings
        ]
    return "\n".join(lines) + "\n"


def package() -> AuditPackage:
    from inspect_audit.package import installed_requirement

    from .rubric import rubric

    return AuditPackage(
        rubric=rubric(),
        formats={"page": lambda: Publisher(skills=[str(HERE / "skills/epoch-page")], build=build)},
        # investigators in child jobs load the rubric skill: they install this package too
        requirement=installed_requirement("inspect-audit-epoch"),
    )
