"""Download exact upstream template files into a verified host cache."""

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import urllib.request
from pathlib import Path
from typing import Any

PIN = Path(__file__).with_name("template.json")


def template() -> Path:
    """Fetch pinned files; private-repository credentials stay on the publishing host."""
    pin = json.loads(PIN.read_text())
    root = (
        Path(
            os.environ.get(
                "INSPECT_AUDIT_TEMPLATE_CACHE", Path.home() / ".cache/inspect-audit/templates"
            )
        )
        / pin["revision"]
    )
    for name, checksum in pin["files"].items():
        path = root / name
        if not path.exists():
            endpoint = f"repos/{pin['repository']}/contents/{name}?ref={pin['revision']}"
            token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
            if not token and shutil.which("gh"):
                result = subprocess.run(
                    ["gh", "api", endpoint, "-H", "Accept: application/vnd.github.raw+json"],
                    capture_output=True,
                    check=False,
                )
                if result.returncode:
                    raise ValueError(
                        "Cannot fetch private audit template: configure gh auth, GH_TOKEN or a verified template cache"
                    )
                content = result.stdout
            else:
                request = urllib.request.Request(
                    "https://api.github.com/" + endpoint,
                    headers={
                        "Accept": "application/vnd.github.raw+json",
                        **({"Authorization": f"Bearer {token}"} if token else {}),
                    },
                )
                with urllib.request.urlopen(request, timeout=60) as response:
                    content = response.read()
            if hashlib.sha256(content).hexdigest() != checksum:
                raise ValueError(f"Template checksum mismatch: {name}")
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
        if path.is_symlink() or hashlib.sha256(path.read_bytes()).hexdigest() != checksum:
            raise ValueError(f"Template checksum mismatch: {name}")
    return root


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
    parser.add_argument("--update-registry", action="store_true")
    args = parser.parse_args()
    upstream = template()
    if args.update_registry:
        from .._framework import REGISTRY

        REGISTRY.write_text(json.dumps(regenerate(upstream), indent=2) + "\n")
    print(upstream)
