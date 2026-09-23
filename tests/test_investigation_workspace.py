"""Remote investigator boundaries and provider selection."""

import asyncio
import io
import json
import tarfile
from pathlib import Path

import pytest
import yaml
from inspect_ai.tool import ToolDef, tool

from inspect_audit import _investigation_workspace as workspace
from inspect_audit._investigate import investigate


def test_hawk_is_default_and_never_silently_runs_locally(monkeypatch):
    monkeypatch.delenv("HAWK_JOB_ID", raising=False)
    with pytest.raises(ValueError, match="defaults to Hawk"):
        investigate(repo="https://example.org/benchmark.git")


def test_hawk_task_uses_kubernetes_without_bind_mounts(monkeypatch, tmp_path):
    from inspect_audit import _investigate

    monkeypatch.setenv("HAWK_JOB_ID", "test-investigation")
    monkeypatch.setattr(_investigate, "register_openrouter_costs", lambda: None)
    task = investigate(
        repo="https://example.org/benchmark.git", revision="abc123",
        audit_package="git+https://example.org/auditor.git@abc123",
        hawk_api_url="https://hawk.example", output_dir=str(tmp_path),
        artifact_dir="s3://bucket/test-investigation/artifacts",
        enforce_cost_limit=False,
    )
    assert task.metadata["execution"] == "hawk"
    assert task.sandbox.type == "k8s"
    values = yaml.safe_load(Path(task.sandbox.config).read_text())
    assert "volumes" not in values["services"]["default"]
    assert values["automountServiceAccountToken"] is False


@pytest.mark.parametrize("name,kind", [("../escape", tarfile.REGTYPE), ("/escape", tarfile.REGTYPE), ("link", tarfile.SYMTYPE), ("hardlink", tarfile.LNKTYPE)])
def test_workspace_rejects_paths_and_links(tmp_path, name, kind):
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w:gz") as archive:
        member = tarfile.TarInfo(name)
        member.type = kind
        member.linkname = "/etc/passwd"
        archive.addfile(member)
    with pytest.raises(ValueError, match="Unsafe"):
        workspace.extract_workspace(stream.getvalue(), tmp_path)
    assert list(tmp_path.iterdir()) == []


def test_valid_workspace_extracts(tmp_path):
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w:gz") as archive:
        member = tarfile.TarInfo("report/findings.json")
        member.size = 2
        archive.addfile(member, io.BytesIO(b"[]"))
    workspace.extract_workspace(stream.getvalue(), tmp_path)
    assert (tmp_path / "report/findings.json").read_text() == "[]"


def test_synchronized_tool_preserves_schema_and_pushes_after_failure(monkeypatch, tmp_path):
    calls = []

    async def pull(root):
        calls.append("pull")

    async def push(root):
        calls.append("push")

    @tool
    def operation():
        async def execute(config: str) -> str:
            """Read configuration.

            Args:
                config: Configuration path.
            """
            calls.append(config)
            raise RuntimeError("failed")
        return execute

    original = operation()
    monkeypatch.setattr(workspace, "pull", pull)
    monkeypatch.setattr(workspace, "push", push)
    wrapped = workspace.synchronized(original, tmp_path)
    assert ToolDef(wrapped).parameters == ToolDef(original).parameters
    with pytest.raises(RuntimeError, match="failed"):
        asyncio.run(wrapped(config="job.yaml"))
    assert calls == ["pull", "job.yaml", "push"]


def test_persistence_keeps_nested_files(tmp_path):
    import fsspec

    workspace.configure(tmp_path, "image@sha256:abc", "memory://audit-test/artifacts")
    settings = tmp_path / "remote-workspace.json"
    settings.write_text(json.dumps({"artifact_dir": "memory://audit-test/artifacts", "sample_uuid": "sample"}))
    source = tmp_path / "publication"
    (source / "nested").mkdir(parents=True)
    (source / "nested/evidence.json").write_text("{}")
    destination = asyncio.run(workspace.persist(tmp_path, source))
    assert destination == "memory://audit-test/artifacts/sample"
    with fsspec.open(destination + "/nested/evidence.json") as file:
        assert file.read() == b"{}"


def test_child_submission_uses_rotated_runner_credentials(monkeypatch, tmp_path):
    import hawk.client
    from inspect_ai.hooks import _hooks

    from inspect_audit import _jobs

    received = {}

    class Hook:
        _current_refresh_token = "old"

    Hook.__module__ = "hawk.runner.refresh_token"
    hook = Hook()

    def refresh(name, value):
        hook._current_refresh_token = "rotated"
        return "current-access"

    class Client:
        def __init__(self, **kwargs):
            received.update(kwargs)

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def create_eval_set(self, config, **kwargs):
            received.update(kwargs)
            return "child-job"

    monkeypatch.setenv("HAWK_JOB_ID", "parent-job")
    monkeypatch.setenv("HAWK_RUNNER_REFRESH_TOKEN", "startup")
    monkeypatch.setattr(_hooks, "override_api_key", refresh)
    monkeypatch.setattr(_hooks, "get_all_hooks", lambda: [hook])
    monkeypatch.setattr(hawk.client, "HawkClient", Client)
    monkeypatch.setattr(_jobs, "parse_config", lambda config: (config, []))
    config = tmp_path / "child.yaml"
    config.write_text("name: child\n")
    assert asyncio.run(_jobs.Hawk("https://hawk.example").submit(config)) == "child-job"
    assert received == {"token": "current-access", "api_url": "https://hawk.example", "refresh_token": "rotated"}
