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
    removes the chance of the agent investigating one commit while the jobs run another.
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
    return distribution_spec("inspect_audit", extra="runner")


def distribution_spec(name: str, extra: str | None = None) -> str | None:
    """The spec that installs an installed distribution, as installed here, in a child job.

    From its PEP 610 record: a git install at its commit, or a local (editable) install
    asked of git; either within its repository's subdirectory when it has one (an audit
    package in `packages/<name>`).
    """
    from importlib.metadata import Distribution, PackageNotFoundError

    try:
        dist = Distribution.from_name(name)
    except PackageNotFoundError:  # pragma: no cover - not installed
        return None
    direct_url = dist.read_text("direct_url.json")
    spec: str | None = None
    if direct_url:
        data = json.loads(direct_url)
        vcs = data.get("vcs_info") or {}
        subdirectory = data.get("subdirectory")
        if vcs.get("vcs") == "git" and vcs.get("commit_id"):
            spec = f"git+{data['url'].removesuffix('.git')}@{vcs['commit_id']}"
        # an editable or local install: the code is a working tree, so ask git
        elif (local := data.get("url", "")).startswith("file://"):
            path = Path(local.removeprefix("file://"))
            spec = git_package_spec(path)
            prefix = subprocess.run(
                ["git", "-C", str(path), "rev-parse", "--show-prefix"],
                capture_output=True,
                text=True,
                check=False,
            ).stdout.strip()
            subdirectory = prefix.rstrip("/") or None
        if spec and subdirectory:
            spec += f"#subdirectory={subdirectory}"
    return f"{name}{f'[{extra}]' if extra else ''} @ {spec}" if spec else None


def locked_extra_pins(repo: str, commit: str, extra: str) -> list[str]:
    """Exact pins, from the benchmark's own lockfile, for what one extra adds.

    Installing `pkg @ git+repo@commit` ignores the repository's `uv.lock` and resolves
    its ranges afresh, so a dependency released since the run (swebench 5 for a run
    locked to 4.1) changes the grader. These pins put the extra's packages back to the
    versions locked at that commit. Only the extra's own additions are pinned: the
    core (Inspect, pydantic, ...) is the job's to set, and pinning it would fight the
    runner. Needs `git` and `uv`; the commit's `pyproject.toml` and `uv.lock` are all
    that is fetched.
    """
    import tempfile

    with tempfile.TemporaryDirectory(prefix="benchmark-lock-") as tmp:
        checkout = Path(tmp)

        def run(*args: str) -> str:
            result = subprocess.run(args, check=False, cwd=checkout, capture_output=True, text=True)
            if result.returncode:
                raise RuntimeError(f"{' '.join(args[:2])} failed: {result.stderr.strip()[-800:]}")
            return result.stdout

        run("git", "init", "-q")
        run("git", "fetch", "-q", "--depth", "1", "--filter=blob:none", repo, commit)
        run("git", "checkout", "-q", "FETCH_HEAD", "--", "pyproject.toml", "uv.lock")

        def export(*flags: str) -> set[str]:
            text = run(
                "uv",
                "export",
                "--frozen",
                "--no-hashes",
                "--no-dev",
                "--no-emit-project",
                "--format",
                "requirements-txt",
                *flags,
            )
            return {
                line.strip()
                for line in text.splitlines()
                if line.strip() and not line.lstrip().startswith(("#", "-e", "."))
            }

        return sorted(export("--extra", extra) - export())


if __name__ == "__main__":
    import sys

    if len(sys.argv) != 4:
        raise SystemExit("usage: python -m inspect_audit.dispatch.packages REPO COMMIT EXTRA")
    for pin in locked_extra_pins(*sys.argv[1:]):
        print(f"- {pin}")
