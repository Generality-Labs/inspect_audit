"""The GL audit framework: one source, `framework.json`.

It is generated from the shipped report template (`_template --update-registry`), and
everything else is derived from it: the definitions in the `audit-framework` skill
(between its generated markers, checked by a test), the files staged into the auditor's
workspace, and the ratings a submission may carry, in either format.
"""

import json
from pathlib import Path
from typing import Any

from inspect_audit.package import Findings, Rubric

HERE = Path(__file__).parent
REGISTRY = HERE / "framework.json"
SKILL = HERE / "skills/audit-framework/SKILL.md"
BEGIN = "<!-- generated from framework.json by `python -m inspect_audit.packages.gl._template --update-registry`: do not edit -->"
END = "<!-- end generated -->"

GUIDE = (
    "Rate in auditor.ratings as the audit-framework skill's Ratings section says "
    "(overall: a grade; dimensions and criteria: assessments), each with explanation and "
    "evidence. Give each broader finding a criterion (e.g. C or C.1); the publisher "
    "generates its finding box."
)

RULE = (
    "ratings.overall takes a grade ({grades}); every dimension (A-I) and criterion "
    "(e.g. C.3) takes an assessment ({assessments}), never a letter grade"
)


def registry() -> dict[str, Any]:
    return json.loads(REGISTRY.read_text())


def stage(target: Path) -> None:
    """Stage analytical data, without report templates or a second judgment store."""
    source = registry()
    (target / "checks.json").write_text(json.dumps(source["dimensions"], indent=2))
    (target / "criteria.json").write_text(
        json.dumps({key: source[key] for key in ("grades", "assessments")}, indent=2)
    )
    (target / "provenance.json").write_text(
        json.dumps({key: source[key] for key in ("repository", "revision", "files")}, indent=2)
    )


def validate(findings: Findings) -> None:
    """Every rating and finding is one the framework defines."""
    source = registry()
    definitions, grades, assessments = (
        source["dimensions"],
        source["grades"],
        source["assessments"],
    )
    rule = RULE.format(grades=", ".join(grades), assessments=", ".join(assessments))
    ratings = findings.auditor.ratings
    if "overall" not in ratings or ratings["overall"].label not in grades:
        raise ValueError(
            "The GL framework needs ratings.overall, with its explanation and evidence, "
            f"before submission: {rule}"
        )
    for key, rating in ratings.items():
        if key == "overall":
            continue
        if key not in definitions:
            raise ValueError(f"Unknown framework criterion {key!r}: IDs are A-I and A.1, C.3, ...")
        if rating.label not in assessments:
            raise ValueError(f"{key}={rating.label} is not an assessment: {rule}")
    for finding in findings.auditor.findings:
        if finding.criterion not in definitions:
            raise ValueError(
                "Each finding needs a framework criterion, e.g. C or C.1, before submission"
            )


def rubric() -> Rubric:
    return Rubric(skill=SKILL.parent, stage=stage, guide=GUIDE, validate=validate)


def skill_definitions(source: dict[str, Any]) -> str:
    """The skill's list of framework IDs and their definitions, from the registry."""
    return "\n".join(
        f"- **{key}: {entry['name']}** — {entry['definition']}"
        for key, entry in source["dimensions"].items()
    )


def write_skill(source: dict[str, Any]) -> None:
    """Replace the skill's generated definitions with the registry's."""
    text = SKILL.read_text()
    head, rest = text.split(BEGIN, 1)
    _, tail = rest.split(END, 1)
    SKILL.write_text(f"{head}{BEGIN}\n{skill_definitions(source)}\n{END}{tail}")
