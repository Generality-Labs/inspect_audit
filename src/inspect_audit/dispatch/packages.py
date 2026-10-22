"""Pip specs a fresh runner installs: the benchmark's checkout and this package."""

import json
import subprocess
from logging import getLogger
from pathlib import Path

logger = getLogger(__name__)


def git_package_spec(repo: Path, revision: str | None = None) -> str | None:
    """The pip spec that installs this checkout's code somewhere else.

    A Hawk runner is a fresh pod: it installs the benchmark to run it. That spec is
    the same repository the agent reads, at the same commit, so deriving it here
    removes the chance of the agent auditing one commit while the jobs run another.
    """
    try:
        origin = subprocess.check_output(
            ["git", "-C", str(repo), "remote", "get-url", "origin"], text=True
        ).strip()
        commit = subprocess.check_output(
            ["git", "-C", str(repo), "rev-parse", "--verify", f"{revision or 'HEAD'}^{{commit}}"],
            text=True,
        ).strip()
    except (subprocess.CalledProcessError, OSError):
        return None
    if not origin.startswith(("https://", "git@", "ssh://")):
        return None
    url = origin.removesuffix(".git").replace("git@github.com:", "https://github.com/")
    on_remote = subprocess.run(
        ["git", "-C", str(repo), "branch", "-r", "--contains", commit],
        capture_output=True,
        text=True,
        check=False,
    )
    if on_remote.returncode != 0 or not on_remote.stdout.strip():
        logger.warning(
            f"{url}@{commit[:8]} is not on any remote branch: a Hawk runner will not be "
            "able to install it. Push the commit, or pass the package spec explicitly."
        )
    return f"git+{url}@{commit}"


def own_package_spec() -> str | None:
    """The spec that installs the inspect_audit a runner should use: this one.

    With the `runner` extra: child jobs need Hawk client dependencies for
    reading logs, without installing the operator CLI and its Inspect pin.
    """
    from importlib.metadata import Distribution, PackageNotFoundError

    try:
        dist = Distribution.from_name("inspect_audit")
    except PackageNotFoundError:  # pragma: no cover - not installed
        return None
    direct_url = dist.read_text("direct_url.json")
    spec: str | None = None
    if direct_url:
        data = json.loads(direct_url)
        vcs = data.get("vcs_info") or {}
        if vcs.get("vcs") == "git" and vcs.get("commit_id"):
            spec = f"git+{data['url'].removesuffix('.git')}@{vcs['commit_id']}"
        # an editable or local install: the code is a working tree, so ask git
        elif (local := data.get("url", "")).startswith("file://"):
            spec = git_package_spec(Path(local.removeprefix("file://")))
    return f"inspect_audit[runner] @ {spec}" if spec else None
