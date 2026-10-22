"""Generated analytical definitions; the actual report template is fetched separately."""

import json
from pathlib import Path

REGISTRY = Path(__file__).parent / "investigation/framework.json"


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
