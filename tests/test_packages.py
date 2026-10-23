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


@pytest.mark.skipif(not shutil.which("uv"), reason="needs uv")
def test_a_generated_investigation_config_is_complete_and_hawk_valid(tmp_path: Path) -> None:
    """Nothing to hand-write: pins, extras, Inspect version and Hawk's address are filled in."""
    from hawk.core.types import EvalSetConfig

    from inspect_audit.dispatch.investigate import investigation_config
    from inspect_audit.dispatch.jobs import INSPECT_VERSION

    repo = tmp_path / "bench"
    shutil.copytree(FIXTURE, repo)
    for args in (["init", "-q"], ["add", "."], ["commit", "-qm", "locked"]):
        subprocess.run(
            ["git", "-c", "user.email=t@t", "-c", "user.name=t", *args], cwd=repo, check=True
        )
    commit = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True, check=True
    ).stdout.strip()

    config = investigation_config(
        benchmark=f"{repo.as_uri()}@{commit}",
        extra="grader",
        task="bench/answers",
        task_args={"split": "test"},
        source="bench.answers",
        samples=["a", "b"],
        checks=["gold-answer"],
        logs=["hawk:run-1"],
        judges={"grader": "openrouter/openai/gpt-6-luna"},
        model="openrouter/openai/gpt-6-luna",
        cost_limit=0.2,
        label="probe",
        one_job_per_sample=True,
        hawk_api_url="https://hawk.example",
        package="inspect_audit[runner] @ git+https://github.com/org/inspect_audit@abc1234",
    )
    EvalSetConfig.model_validate(config)
    packages = [p.split(";")[0].strip() for p in config["packages"]]
    assert f"bench[grader] @ git+{repo.as_uri()}@{commit}" in packages
    assert "idna==3.7" in packages and "six==1.16.0" not in packages  # the extra only
    assert f"inspect-ai=={INSPECT_VERSION}" in packages
    assert config["runner"]["environment"]["HAWK_API_URL"] == "https://hawk.example"
    items = config["tasks"][0]["items"]
    assert [i["args"]["samples"] for i in items] == [["a"], ["b"]]
    assert items[0]["args"]["logs"] == ["hawk:run-1"]


def test_the_package_pins_the_inspect_every_job_runs_on() -> None:
    """One Inspect version: the package's own requirement and the jobs' pin cannot drift apart."""
    import tomllib

    from inspect_audit.dispatch.jobs import INSPECT_VERSION

    project = tomllib.loads((Path(__file__).parents[1] / "pyproject.toml").read_text())
    assert f"inspect-ai=={INSPECT_VERSION}" in project["project"]["dependencies"]


def test_the_investigation_task_fills_its_own_plumbing(monkeypatch: pytest.MonkeyPatch) -> None:
    """No logs key needed, prices looked up for a cost limit, the published image by default."""
    import inspect_ai.model

    from inspect_audit import _registry
    from inspect_audit.containers import INVESTIGATOR_IMAGE

    built: dict[str, object] = {}
    from inspect_ai import Task
    from inspect_ai.dataset import Sample

    def capture(task: str, **kw: object) -> Task:
        built.update(kw)
        return Task(dataset=[Sample(input="q")])

    monkeypatch.setattr(_registry, "investigation_task", capture)
    monkeypatch.setattr(inspect_ai.model, "get_model", lambda: "openrouter/openai/gpt-6-luna")
    asked: list[list[str]] = []

    def prices(models: list[str]) -> dict[str, dict[str, float]]:
        asked.append(models)
        return {m: {"input": 0.1, "output": 0.5} for m in models}

    monkeypatch.setattr(_registry, "prices_for_models", prices)
    registered: list[dict[str, dict[str, float]]] = []
    monkeypatch.setattr(_registry, "register_prices", registered.append)

    _registry.investigation(
        task="b/t",
        samples=["1"],
        checks=["gold-answer"],
        logs=[],
        cost_limit=0.2,
        judge={"grader": "openrouter/openai/judge"},
    )
    assert built["logs"] is None
    assert built["investigator_image"] == INVESTIGATOR_IMAGE
    assert asked == [["openrouter/openai/gpt-6-luna", "openrouter/openai/judge"]]
    assert set(registered[0]) == {"openrouter/openai/gpt-6-luna", "openrouter/openai/judge"}

    _registry.investigation(
        task="b/t", samples=["1"], checks=["gold-answer"], investigator_image="build"
    )
    assert built["investigator_image"] is None
