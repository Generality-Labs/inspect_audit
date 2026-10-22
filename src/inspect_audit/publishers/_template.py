"""Download exact upstream template files into a verified host cache."""

import hashlib
import json
import os
import shutil
import subprocess
import urllib.request
from pathlib import Path

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


if __name__ == "__main__":
    print(template())
