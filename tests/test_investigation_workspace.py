"""Remote investigator boundaries and provider selection."""

import asyncio
import io
import json
import tarfile
from pathlib import Path

import pytest
import yaml
from inspect_ai.tool import ToolDef, ToolError, tool

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
    monkeypatch.setattr(_investigate.tempfile, "gettempdir", lambda: str(tmp_path))
    task = investigate(
        repo="https://example.org/benchmark.git", revision="abc123",
        audit_package="git+https://example.org/auditor.git@abc123",
        hawk_api_url="https://hawk.example",
        artifact_dir="s3://bucket/test-investigation/artifacts",
        enforce_cost_limit=False,
    )
    assert task.metadata["execution"] == "hawk"
    assert Path(task.metadata["investigation_dir"]).parent == tmp_path / "inspect-audit"
    assert task.sandbox.type == "k8s"
    values = yaml.safe_load(Path(task.sandbox.config).read_text())
    assert "volumes" not in values["services"]["default"]
    assert values["automountServiceAccountToken"] is False


@pytest.mark.parametrize("name,kind", [("../escape", tarfile.REGTYPE), ("/escape", tarfile.REGTYPE), ("link", tarfile.SYMTYPE), ("hardlink", tarfile.LNKTYPE), ("fifo", tarfile.FIFOTYPE)])
def test_workspace_skips_paths_and_links_without_failing(tmp_path, name, kind):
    """A venv or a cloned repo makes links; they are left out, never fatal."""
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w:gz") as archive:
        member = tarfile.TarInfo(name)
        member.type = kind
        member.linkname = "/etc/passwd"
        archive.addfile(member)
        kept = tarfile.TarInfo("report/findings.json")
        kept.size = 2
        archive.addfile(kept, io.BytesIO(b"[]"))
    skipped = workspace.extract_workspace(stream.getvalue(), tmp_path)
    assert skipped == [name]
    assert (tmp_path / "report/findings.json").read_text() == "[]"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["report"]


def test_valid_workspace_extracts(tmp_path):
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w:gz") as archive:
        member = tarfile.TarInfo("report/findings.json")
        member.size = 2
        archive.addfile(member, io.BytesIO(b"[]"))
    workspace.extract_workspace(stream.getvalue(), tmp_path)
    assert (tmp_path / "report/findings.json").read_text() == "[]"


def _sync_recorder(monkeypatch, calls, fail_push=False):
    async def pull(root):
        calls.append("pull")
        return ["not mirrored (links or special files): env/bin/python"]

    async def push(root):
        calls.append("push")
        if fail_push:
            raise RuntimeError("tar timed out")

    async def save_state(root):
        calls.append("save_state")

    monkeypatch.setattr(workspace, "pull", pull)
    monkeypatch.setattr(workspace, "push", push)
    monkeypatch.setattr(workspace, "save_state", save_state)


@tool
def operation(fail: bool = False):
    async def execute(config: str) -> str:
        """Read configuration.

        Args:
            config: Configuration path.
        """
        if fail:
            raise RuntimeError("failed")
        return f"submitted {config}"
    return execute


def test_synchronized_tool_preserves_schema_and_pushes_after_failure(monkeypatch, tmp_path):
    calls = []
    _sync_recorder(monkeypatch, calls)
    original = operation(fail=True)
    wrapped = workspace.synchronized(original, tmp_path)
    assert ToolDef(wrapped).parameters == ToolDef(original).parameters
    with pytest.raises(RuntimeError, match="failed"):
        asyncio.run(wrapped(config="job.yaml"))
    assert calls == ["pull", "push", "save_state"]


def test_sync_failure_is_reported_beside_a_successful_result(monkeypatch, tmp_path):
    """A push failure after a submission must not read as a failed submission."""
    calls = []
    _sync_recorder(monkeypatch, calls, fail_push=True)
    result = asyncio.run(workspace.synchronized(operation(), tmp_path)(config="job.yaml"))
    assert result.startswith("submitted job.yaml")
    assert "push after this call failed: tar timed out" in result
    assert "env/bin/python" in result
    assert calls == ["pull", "push", "save_state"]


def _remote_root(tmp_path, base="memory://audit-test/artifacts"):
    workspace.configure(tmp_path, "image@sha256:abc", base)
    (tmp_path / "remote-workspace.json").write_text(json.dumps({"artifact_dir": base, "sample_uuid": "sample"}))
    return tmp_path


def test_persistence_keeps_nested_files_and_versions_publications(tmp_path):
    import fsspec

    root = _remote_root(tmp_path)
    for version, name in (("v1", "fig-old.png"), ("v2", "report.pdf")):
        source = tmp_path / version
        (source / "nested").mkdir(parents=True)
        (source / "nested/evidence.json").write_text("{}")
        (source / name).write_text(version)
        destination = asyncio.run(workspace.persist(root, source, f"published/{version}"))
        assert destination == f"memory://audit-test/artifacts/sample/published/{version}"
        with fsspec.open(destination + "/nested/evidence.json") as file:
            assert file.read() == b"{}"
    fs = fsspec.filesystem("memory")
    assert not fs.exists("/audit-test/artifacts/sample/published/v2/fig-old.png")


def test_state_survives_the_pod_and_uploads_only_changes(tmp_path, monkeypatch):
    import fsspec

    root = _remote_root(tmp_path, "memory://audit-state/artifacts")
    (root / "jobs.json").write_text('{"jobs": []}')
    (root / "local_spend.json").write_text('{"prior_usd": 0, "this_run_usd": 1.5}')
    (root / "work/report").mkdir(parents=True)
    (root / "work/report/findings.json").write_text("[]")
    (root / "work/journal.md").write_text("# journal")
    asyncio.run(workspace.save_state(root))
    fs = fsspec.filesystem("memory")
    base = "/audit-state/artifacts/sample/state"
    assert fs.cat(f"{base}/local_spend.json") == b'{"prior_usd": 0, "this_run_usd": 1.5}'
    assert fs.cat(f"{base}/work/report/findings.json") == b"[]"
    assert fs.cat(f"{base}/work/journal.md") == b"# journal"

    uploaded = []
    monkeypatch.setattr(workspace, "_upload", lambda files, destination: uploaded.append(sorted(files)))
    asyncio.run(workspace.save_state(root))
    assert uploaded == []
    (root / "jobs.json").write_text('{"jobs": [1]}')
    asyncio.run(workspace.save_state(root))
    assert uploaded == [["jobs.json"]]


def test_workspace_file_fetch_refuses_escapes(tmp_path):
    (tmp_path / "work").mkdir()
    (tmp_path / "secret.yaml").write_text("x: 1")
    (tmp_path / "work" / "ok.yaml").write_text("x: 2")
    assert asyncio.run(workspace.fetch_workspace_file(tmp_path, "/workspace/ok.yaml")).read_text() == "x: 2"
    for bad in ("/workspace/../secret.yaml", "/etc/passwd", "/workspace/missing.yaml"):
        with pytest.raises(ToolError):
            asyncio.run(workspace.fetch_workspace_file(tmp_path, bad))
    (tmp_path / "work" / "link.yaml").symlink_to(tmp_path / "secret.yaml")
    with pytest.raises(ToolError):
        asyncio.run(workspace.fetch_workspace_file(tmp_path, "/workspace/link.yaml"))


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
            # hawk >= 3.5 returns the API body rather than the bare id
            return {"eval_set_id": "child-job", "warnings": []}

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


def test_hawk_cli_is_the_venvs_own_not_paths(monkeypatch, tmp_path):
    """Runner images put a hawk without keyring first on PATH."""
    import sys

    from inspect_audit import _jobs

    fake = tmp_path / "bin"
    fake.mkdir()
    (fake / "python").write_text("")
    (fake / "hawk").write_text("")
    monkeypatch.setattr(sys, "executable", str(fake / "python"))
    assert _jobs.Hawk("https://hawk.example").binary == str(fake / "hawk")
    (fake / "hawk").unlink()
    assert _jobs.Hawk("https://hawk.example").binary == "hawk"


def test_an_audit_job_on_hawk_registers_prices_before_hawk_applies_them(monkeypatch):
    """Hawk's set_model_cost refuses models Inspect has no entry for (the gpt-6 children died so)."""
    from inspect_audit import _investigate, _registry

    class Registered(Exception):
        pass

    def register():
        raise Registered

    monkeypatch.setattr(_investigate, "register_openrouter_costs", register)
    monkeypatch.setenv("HAWK_JOB_ID", "inv-child")
    with pytest.raises(Registered):
        _registry.audit.__wrapped__(task="bench/Chess Puzzles")  # type: ignore[attr-defined]
