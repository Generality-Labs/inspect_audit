"""Stage the GL analytical framework without a second assessment store."""

import json
import re
import shutil
from pathlib import Path


def framework_definitions(report: Path) -> dict[str, dict[str, str]]:
    """Read names AND meanings from the pinned GL registry without a second taxonomy."""
    source = (report / "framework/auditframework.sty").read_text()
    source = re.sub(r"(?m)^\s*%.*$", "", source)
    source = source.replace("%\n", "\n")
    letters = dict(re.findall(r"\\dimensionletter\{([^}]+)\}\{([^}]+)\}", source))
    checks: dict[str, dict[str, str]] = {}
    for block in re.split(r"\\definedimension\{", source)[1:]:
        match = re.match(r"([^}]+)\}\{([^}]+)\}\{([^}]+)\}\{([^}]+)\}", block)
        if match is None or match[1] not in letters:
            continue
        letter = letters[match[1]]
        checks[letter] = {
            "name": match[2],
            "definition": " ".join(match[4].split()),
            "dimension": match[1],
        }
        for index, (name, meaning) in enumerate(
            re.findall(r"\\gsub\{([^}]+)\}\{([^}]+)\}", block), 1
        ):
            checks[f"{letter}.{index}"] = {
                "name": name.replace(r"\&", "&"),
                "definition": " ".join(meaning.split()),
                "dimension": match[1],
            }
    if len([key for key in checks if "." not in key]) != 9:
        raise ValueError("Expected all nine dimensions in the pinned framework")
    return checks


def stage_framework(analysis: Path) -> None:
    """Stage reference definitions; judgments are recorded only in findings.json."""
    analysis.mkdir(parents=True, exist_ok=True)
    shutil.copytree(Path(__file__).parent / "investigation/framework", analysis / "framework")
    (analysis / "framework/checks.json").write_text(
        json.dumps(framework_definitions(analysis), indent=2) + "\n"
    )
