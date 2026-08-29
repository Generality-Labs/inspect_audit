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
    phoenix_benchmark,
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

    # a liveness probe fires first, then the git revert, then setup
    assert len(calls) == 3
    assert calls[0] == "true"
    assert "git" in calls[1] and "reset" in calls[1]
    assert calls[2] == "echo setup"


def test_restore_raises_on_an_unreachable_box_so_the_caller_can_rebuild(monkeypatch) -> None:
    """A soft reset runs inside the box; a dead box must raise, not report success.

    Otherwise an image-baked benchmark (no setup script) would git-noop, setup-noop
    and falsely claim a bricked box was restored -- the caller's phoenix fallback
    never fires. The liveness probe returning non-success is the brick signal.
    """

    class DeadBox:
        async def exec(self, cmd, timeout=None, input=None):  # noqa: D102
            return SimpleNamespace(success=False, stdout="", stderr="no such container")

    monkeypatch.setattr(sandbox_module, "sandbox_env", lambda name=None: DeadBox())

    with pytest.raises(RuntimeError, match="unreachable"):
        asyncio.run(restore_benchmark(None))  # script=None: the case that used to slip


def _patch_phoenix(
    monkeypatch,
    *,
    services: dict,
    running: list[str],
    exited_ok: list[str] = (),
    is_docker: bool = True,
    setup_error: str | None = None,
) -> dict:
    """Wire phoenix_benchmark's compose calls to fakes; return a record of them."""
    calls: dict = {"setup": []}
    project = SimpleNamespace(name="proj", config="/x/compose.yaml", env={})

    class FakeDockerEnv:
        # a proxy-like env: as_type unwraps to a docker env carrying the project
        _project = project

        def as_type(self, _cls):
            return self

    class NonDockerEnv:
        def as_type(self, _cls):
            raise TypeError("not a docker sandbox")

    box = FakeDockerEnv() if is_docker else NonDockerEnv()
    monkeypatch.setattr(sandbox_module, "sandbox_env", lambda name=None: box)

    async def fake_services(project):
        return services

    async def fake_command(cmd, *, project=None, timeout=None, timeout_retry=True):
        calls["cmd"] = cmd
        return SimpleNamespace(success=True, stdout="", stderr="boom")

    async def fake_ps(project=None, status=None, all=False):
        # compose_ps returns the whole project; the auditor `default` is always up.
        # branch on status the way the real one does, so the exit-0 path is exercised.
        calls.setdefault("ps_status", []).append(status)
        if status == "running":
            return [{"Service": name} for name in ["default", *running]]
        if status == "exited":
            return [{"Service": name, "ExitCode": 0} for name in exited_ok]
        return []

    async def fake_setup(script):
        calls["setup"].append(script)
        if setup_error is not None:
            raise RuntimeError(setup_error)

    monkeypatch.setattr(sandbox_module, "compose_services", fake_services)
    monkeypatch.setattr(sandbox_module, "compose_command", fake_command)
    monkeypatch.setattr(sandbox_module, "compose_ps", fake_ps)
    monkeypatch.setattr(sandbox_module, "run_benchmark_setup", fake_setup)
    return calls


def test_phoenix_force_recreates_only_the_benchmark_service(monkeypatch) -> None:
    """The rebuild names the benchmark service and never touches the auditor."""
    calls = _patch_phoenix(
        monkeypatch, services={"default": {}, "benchmark": {}}, running=["benchmark"]
    )

    summary = asyncio.run(phoenix_benchmark("echo setup"))

    cmd = calls["cmd"]
    assert "up" in cmd and "--force-recreate" in cmd
    assert "benchmark" in cmd
    assert "default" not in cmd  # the auditor is never recreated
    assert "down" not in cmd  # never project-wide
    assert "--no-deps" not in cmd  # naming the set replaces the need for it
    assert calls["setup"] == ["echo setup"]  # per-sample state repopulated after
    assert "rebuilt from image" in summary


def test_phoenix_recreates_every_non_auditor_service(monkeypatch) -> None:
    """CTF-style siblings (victim, writer) a brick can take with it are rebuilt too."""
    calls = _patch_phoenix(
        monkeypatch,
        services={"default": {}, "benchmark": {}, "victim": {}},
        running=["benchmark", "victim"],
    )

    asyncio.run(phoenix_benchmark(None))

    cmd = calls["cmd"]
    assert "benchmark" in cmd and "victim" in cmd
    assert "default" not in cmd


def test_phoenix_raises_when_the_box_does_not_come_back(monkeypatch) -> None:
    """A brick that reached past the container leaves nothing running -- a finding."""
    _patch_phoenix(
        monkeypatch, services={"default": {}, "benchmark": {}}, running=[]
    )

    with pytest.raises(RuntimeError, match="did not come back"):
        asyncio.run(phoenix_benchmark("echo setup"))


def test_phoenix_is_unsupported_off_docker(monkeypatch) -> None:
    _patch_phoenix(
        monkeypatch,
        services={"default": {}, "benchmark": {}},
        running=["benchmark"],
        is_docker=False,
    )

    with pytest.raises(RuntimeError, match="needs the docker sandbox"):
        asyncio.run(phoenix_benchmark("echo setup"))


def test_phoenix_reports_state_a_rebuild_cannot_reset(monkeypatch) -> None:
    """Named volumes / bind-mounts survive force-recreate; say so, don't imply clean."""
    calls = _patch_phoenix(
        monkeypatch,
        services={"default": {}, "benchmark": {"volumes": ["vol:/data"]}},
        running=["benchmark"],
    )

    summary = asyncio.run(phoenix_benchmark("echo setup"))

    assert "volumes" in summary and "benchmark" in summary
    assert calls["cmd"]  # still recreated


def test_phoenix_needs_a_benchmark_box(monkeypatch) -> None:
    _patch_phoenix(monkeypatch, services={"default": {}}, running=[])

    with pytest.raises(RuntimeError, match="no benchmark box"):
        asyncio.run(phoenix_benchmark("echo setup"))


def test_phoenix_surfaces_a_setup_failure_as_itself(monkeypatch) -> None:
    """A setup failure on the fresh box is reported as itself, not 'did not come back'.

    The box did come back; re-seeding it is what failed, and the message says so.
    """
    _patch_phoenix(
        monkeypatch,
        services={"default": {}, "benchmark": {}},
        running=["benchmark"],
        setup_error="Benchmark setup failed: boom",
    )

    with pytest.raises(RuntimeError, match="setup failed"):
        asyncio.run(phoenix_benchmark("echo setup"))


def test_phoenix_counts_a_clean_one_shot_service_as_up(monkeypatch) -> None:
    """A CTF-style writer that plants state and exits 0 is up, not a failed rebuild.

    Checking only status=running would flag such a sibling as "did not come back"
    and raise a spurious brick finding on a healthy box, so exited-code-0 counts too.
    """
    calls = _patch_phoenix(
        monkeypatch,
        services={"default": {}, "benchmark": {}, "writer": {}},
        running=["benchmark"],
        exited_ok=["writer"],
    )

    summary = asyncio.run(phoenix_benchmark("echo setup"))

    assert "rebuilt from image" in summary  # writer's clean exit did not trip it
    assert "exited" in calls["ps_status"]  # the exit-0 path was actually consulted


def test_phoenix_relays_sample_files_then_runs_setup(monkeypatch) -> None:
    """A rebuilt box is empty, so file-delivered per-sample state must be re-laid.

    Restoring only the setup script would silently drop a fixture/data file the
    evaluated agent started with. Files go in first, then setup, as sample-init did.
    """
    calls = _patch_phoenix(
        monkeypatch, services={"default": {}, "benchmark": {}}, running=["benchmark"]
    )
    order: list[str] = []
    copied: dict = {}

    async def fake_read(src):
        return f"bytes:{src}".encode()

    async def fake_copy(contents, envs):
        order.append("files")
        copied["contents"] = contents
        copied["envs"] = list(envs)

    monkeypatch.setattr(sandbox_module, "resolve_sample_files", lambda f: f)
    monkeypatch.setattr(sandbox_module, "read_sandboxenv_file", fake_read)
    monkeypatch.setattr(sandbox_module, "copy_sandbox_environment_files", fake_copy)

    async def ordered_setup(script):
        order.append("setup")
        calls["setup"].append(script)

    monkeypatch.setattr(sandbox_module, "run_benchmark_setup", ordered_setup)

    asyncio.run(
        phoenix_benchmark("echo setup", {"benchmark:/work/given.txt": "/host/given.txt"})
    )

    assert copied["contents"] == {"benchmark:/work/given.txt": b"bytes:/host/given.txt"}
    assert "benchmark" in copied["envs"]  # keyed for the prefix to resolve against
    assert order == ["files", "setup"]  # files first, then setup


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
