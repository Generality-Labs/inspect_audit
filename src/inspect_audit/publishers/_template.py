"""Laurence's GL audit template, shipped with this package.

`template/` holds the files of `Generality-Labs/audit_reports` the LaTeX publisher
builds from, copied at the revision named in `template.json`. Shipped rather than
fetched: that repository is private, and fetching would need a GitHub token wherever
a report is built. To move to a newer template: `--update-from <checkout>`, then
`--update-registry`.
"""

import argparse
import json
import re
import subprocess
from pathlib import Path
from typing import Any

PIN = Path(__file__).with_name("template.json")
SHIPPED = Path(__file__).with_name("template")


def template() -> Path:
    """The shipped template."""
    return SHIPPED


def update_from(checkout: Path) -> None:
    """Copy the template files from a checkout of audit_reports and record its revision."""
    pin = json.loads(PIN.read_text())
    for name in pin["files"]:
        (SHIPPED / name).parent.mkdir(parents=True, exist_ok=True)
        (SHIPPED / name).write_bytes((checkout / name).read_bytes())
    revision = subprocess.run(
        ["git", "-C", str(checkout), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    PIN.write_text(json.dumps({**pin, "revision": revision}, indent=2) + "\n")


def regenerate(upstream: Path) -> dict[str, Any]:
    """Derive the complete registry from the verified pin, without hand-maintained names."""
    style = (upstream / "template/auditframework.sty").read_text()
    style = re.sub(r"(?m)^\s*%.*$", "", style).replace("%\n", "\n")
    letters = dict(re.findall(r"\\dimensionletter\{([^}]+)\}\{([^}]+)\}", style))
    bases = dict(re.findall(r"\\dimensionsubbase\{([^}]+)\}\{(-?\d+)\}", style))
    dimensions = {}
    for block in re.split(r"\\definedimension\{", style)[1:]:
        match = re.match(r"([^}]+)\}\{([^}]+)\}\{([^}]+)\}\{([^}]+)\}", block)
        if match is None or match[1] not in letters:
            continue
        letter = letters[match[1]]
        dimensions[letter] = {
            "name": match[2],
            "definition": " ".join(match[4].split()),
            "dimension": match[1],
        }
        for index, (name, meaning) in enumerate(
            re.findall(r"\\gsub\{([^}]+)\}\{([^}]+)\}", block), int(bases.get(match[1], "0")) + 1
        ):
            dimensions[f"{letter}.{index}"] = {
                "name": name.replace(r"\&", "&"),
                "definition": " ".join(meaning.split()),
                "dimension": match[1],
            }
    grades = {
        grade: " ".join(description.split())
        for grade, description in re.findall(
            r"\\newcommand\{\\gradetext([A-D])\}\{([^}]+)\}", style
        )
    }
    criteria = (upstream / "template/sections/ScoringCriteria.tex").read_text()
    assessments = {
        {"NotAssessed": "Not assessed", "NA": "Not applicable"}.get(macro, macro): " ".join(
            description.split()
        )
        for macro, description in re.findall(
            r"\\assess(None|Minor|Major|Critical|NotAssessed|NA)\s*&\s*([^&]+?)\\\\ \\hline",
            criteria,
        )
    }
    if (
        set(dimensions) & set("ABCDEFGHI") != set("ABCDEFGHI")
        or set(grades) != set("ABCD")
        or len(assessments) != 6
    ):
        raise ValueError(
            "Pinned template does not contain the expected dimensions, grades and assessment scale"
        )
    return {
        **json.loads(PIN.read_text()),
        "dimensions": dimensions,
        "grades": grades,
        "assessments": assessments,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--update-from", type=Path, help="a checkout of audit_reports")
    parser.add_argument("--update-registry", action="store_true")
    args = parser.parse_args()
    if args.update_from:
        update_from(args.update_from)
    upstream = template()
    if args.update_registry:
        from .._framework import REGISTRY

        REGISTRY.write_text(json.dumps(regenerate(upstream), indent=2) + "\n")
    print(upstream)
