"""A package from outside inspect_audit: a rubric of two Pass/Flag questions, one format."""

import json
from pathlib import Path

from inspect_audit.package import AuditPackage, Findings, Publisher, Rubric

QUESTIONS = ("q1", "q2")


def stage(target: Path) -> None:
    (target / "questions.json").write_text(json.dumps(list(QUESTIONS)))


def validate(findings: Findings) -> None:
    ratings = findings.auditor.ratings
    if missing := [q for q in QUESTIONS if q not in ratings]:
        raise ValueError(f"the toy rubric needs ratings for {', '.join(missing)}")
    if bad := [q for q, r in ratings.items() if r.label not in ("Pass", "Flag")]:
        raise ValueError(f"toy ratings are Pass or Flag: {', '.join(bad)}")


async def build(root: Path, bundle: Path) -> Path:
    findings = json.loads((bundle / "findings.json").read_text())
    out = root / "toy-report"
    out.mkdir()
    verdict = (
        "Flawed"
        if any(r["label"] == "Flag" for r in findings["auditor"]["ratings"].values())
        else "Verified"
    )
    (out / "report.md").write_text(f"# {verdict}\n")
    return out


def package() -> AuditPackage:
    return AuditPackage(
        rubric=Rubric(
            skill=Path(__file__).with_name("toy-rubric"),
            stage=stage,
            guide="Rate q1 and q2 as the toy-rubric skill says.",
            validate=validate,
        ),
        formats={"page": lambda: Publisher(skills=[], build=build)},
        requirement="toy-package @ git+https://example.org/toy@0123456",
    )
