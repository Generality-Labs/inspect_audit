"""The GL audit framework: one source, `audit/framework.json`.

It is generated from the shipped report template (`publishers._template`,
`--update-registry`), and everything else is derived from it: the definitions in the
`audit-framework` skill (between its generated markers, checked by a test), the files
staged into the auditor's workspace, and the labels the LaTeX publisher accepts.
"""

import json
from pathlib import Path
from typing import Any

REGISTRY = Path(__file__).parent / "audit/framework.json"


def framework_definitions(analysis: Path) -> dict[str, dict[str, str]]:
    return json.loads((analysis / "framework/checks.json").read_text())


def stage_framework(analysis: Path) -> None:
    """Stage analytical data, without report templates or a second judgment store."""
    source = json.loads(REGISTRY.read_text())
    target = analysis / "framework"
    target.mkdir(parents=True)
    (target / "checks.json").write_text(json.dumps(source["dimensions"], indent=2))
    (target / "criteria.json").write_text(
        json.dumps({key: source[key] for key in ("grades", "assessments")}, indent=2)
    )
    (target / "provenance.json").write_text(
        json.dumps({key: source[key] for key in ("repository", "revision", "files")}, indent=2)
    )


SKILL = Path(__file__).parent / "skills/audit-framework/SKILL.md"
BEGIN = "<!-- generated from audit/framework.json by `python -m inspect_audit.publishers._template --update-registry`: do not edit -->"
END = "<!-- end generated -->"


def skill_definitions(registry: dict[str, Any]) -> str:
    """The skill's list of framework IDs and their definitions, from the registry."""
    return "\n".join(
        f"- **{key}: {entry['name']}** — {entry['definition']}"
        for key, entry in registry["dimensions"].items()
    )


def write_skill(registry: dict[str, Any]) -> None:
    """Replace the skill's generated definitions with the registry's."""
    text = SKILL.read_text()
    head, rest = text.split(BEGIN, 1)
    _, tail = rest.split(END, 1)
    SKILL.write_text(f"{head}{BEGIN}\n{skill_definitions(registry)}\n{END}{tail}")
