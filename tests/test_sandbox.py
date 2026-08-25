"""The benchmark environment: reproduction, restoration, and unit conversion.

No Docker: these exercise the compose synthesis and the restore ordering with a
fake sandbox, so the contract is checked without spinning up containers.
"""

import asyncio
import logging
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml
from inspect_ai import Task
from inspect_ai.dataset import MemoryDataset, Sample
from inspect_ai.scorer import match
from inspect_ai.util import SandboxEnvironmentSpec

import inspect_audit._sandbox as sandbox_module
from inspect_audit._sandbox import (
    _seconds,
    audit_compose,
    has_benchmark,
    restore_benchmark,
)


def make_task() -> Task:
    dataset = MemoryDataset([Sample(input="question", target="answer")])
    return Task(name="fixture_task", dataset=dataset, scorer=match())


def test_restore_reverts_git_state_before_rerunning_setup(monkeypatch) -> None:
    """Revert first, then setup.

    Setup routinely writes untracked files into a repo; `git clean` running after
    it would wipe the very state setup just recreated, returning the box to the
    image's state rather than the sample's.
    """
    calls: list[str] = []

    class FakeBox:
        async def exec(self, cmd, timeout=None, input=None):  # noqa: D102
            calls.append(cmd[-1])
            return SimpleNamespace(success=True, stdout="", stderr="")

    monkeypatch.setattr(sandbox_module, "sandbox_env", lambda name=None: FakeBox())

    asyncio.run(restore_benchmark("echo setup"))

    assert len(calls) == 2
    assert "git" in calls[0] and "reset" in calls[0]
    assert calls[1] == "echo setup"


def test_a_dockerfile_environment_is_reproduced_not_dropped(tmp_path: Path) -> None:
    """A Dockerfile sandbox becomes a benchmark service, matching inspect's own run.

    Inspect's auto-generated compose for a Dockerfile builds it and runs it with
    `network_mode: none`; an audit that silently dropped that box would audit the
    question without the environment the attempts actually ran in.
    """
    dockerfile = tmp_path / "Dockerfile"
    dockerfile.write_text("FROM python:3.12-slim\n")
    spec = SandboxEnvironmentSpec("docker", str(dockerfile))
    assert has_benchmark(spec)

    sandbox = audit_compose(make_task(), spec, stage=tmp_path / "stage")
    services = yaml.safe_load(Path(str(sandbox[1])).read_text())["services"]

    assert set(services) == {"default", "benchmark"}
    assert services["benchmark"]["build"] == {
        "context": str(tmp_path),
        "dockerfile": "Dockerfile",
    }
    # isolation parity: the eval ran the box with no network, so must the audit
    assert services["benchmark"]["network_mode"] == "none"


def test_a_bare_docker_environment_is_reproduced(tmp_path: Path) -> None:
    """`sandbox="docker"` ran the generic tool-support image; the audit reruns it."""
    spec = SandboxEnvironmentSpec("docker")
    assert has_benchmark(spec)

    sandbox = audit_compose(make_task(), spec, stage=tmp_path / "stage")
    services = yaml.safe_load(Path(str(sandbox[1])).read_text())["services"]

    assert set(services) == {"default", "benchmark"}
    assert services["benchmark"]["image"] == "aisiuk/inspect-tool-support"
    assert services["benchmark"]["network_mode"] == "none"


def test_an_unreproducible_environment_warns_rather_than_silently_dropping(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    spec = SandboxEnvironmentSpec("docker", str(tmp_path / "missing-compose.yaml"))
    assert not has_benchmark(spec)

    with caplog.at_level(logging.WARNING, logger="inspect_audit._sandbox"):
        sandbox = audit_compose(make_task(), spec, stage=tmp_path / "stage")

    assert "without the benchmark environment" in caplog.text
    # the auditor still stands alone rather than failing the audit outright
    assert Path(str(sandbox[1])).name == "compose.yaml"


def test_no_declared_environment_stands_alone_without_warning(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.WARNING, logger="inspect_audit._sandbox"):
        audit_compose(make_task(), None, stage=tmp_path / "stage")
    assert caplog.text == ""


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("90s", 90),
        ("1m30s", 90),
        ("2h", 7200),
        ("500ms", 1),  # sub-second rounds up, never to zero
        ("1.5s", 2),
        ("0s", 0),
        ("junk", None),
        ("", None),
    ],
)
def test_seconds_parses_compose_durations(value: str, expected: int | None) -> None:
    assert _seconds(value) == expected
