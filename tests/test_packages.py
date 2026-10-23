"""Benchmark dependencies come back pinned to the benchmark's own lockfile."""

import shutil
import subprocess
from pathlib import Path

import pytest

from inspect_audit.dispatch.packages import locked_extra_pins

FIXTURE = Path(__file__).parent / "fixtures" / "locked_benchmark"


@pytest.mark.skipif(not shutil.which("uv"), reason="needs uv")
def test_an_extra_is_pinned_to_the_commit_lock_not_resolved_afresh(tmp_path: Path) -> None:
    """The extra's additions come from uv.lock at that commit; the core is left to the job."""
    repo = tmp_path / "bench"
    shutil.copytree(FIXTURE, repo)

    def git(*args: str) -> str:
        return subprocess.run(
            ["git", "-c", "user.email=t@t", "-c", "user.name=t", *args],
            cwd=repo,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()

    git("init", "-q")
    git("add", ".")
    git("commit", "-qm", "locked")
    commit = git("rev-parse", "HEAD")
    # a later commit loosens nothing in the file we read: the pinned commit is what counts
    (repo / "uv.lock").write_text((repo / "uv.lock").read_text().replace("3.7", "9.9"))
    git("commit", "-qam", "later")

    pins = locked_extra_pins(repo.as_uri(), commit, "grader")
    assert [p.split(";")[0].strip() for p in pins] == ["idna==3.7"]
