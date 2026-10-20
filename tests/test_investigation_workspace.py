"""The investigator's box, and the host's trusted copy of what is in it."""

import asyncio
import io
import json
import tarfile
from pathlib import Path

import pytest
import yaml
from inspect_ai.tool import tool

from inspect_audit import _investigation_workspace as workspace
from inspect_audit._investigate import investigate


@pytest.mark.parametrize(
    ("name", "kind"),
    [
        ("../escape", tarfile.REGTYPE),
        ("/escape", tarfile.REGTYPE),
        ("link", tarfile.SYMTYPE),
        ("hardlink", tarfile.LNKTYPE),
        ("fifo", tarfile.FIFOTYPE),
    ],
)
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


def _remote_root(tmp_path, base="memory://audit-test/artifacts"):
    workspace.configure(tmp_path, "image@sha256:abc")
    (tmp_path / "artifacts.json").write_text(
        json.dumps({"artifact_dir": base, "sample_key": "sample"})
    )
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


def test_the_pull_guard_follows_the_sandboxs_own_read_limit(monkeypatch):
    """Hawk raises the read limit to 1 GiB; Inspect applies it when an eval starts."""
    from inspect_ai.util._sandbox.limits import reset_sandbox_limits, set_sandbox_limits

    assert workspace.max_pull_bytes() < 100 * 1024**2
    monkeypatch.setenv("INSPECT_SANDBOX_MAX_READ_FILE_SIZE", str(1024**3))
    tokens = set_sandbox_limits()
    try:
        assert workspace.max_pull_bytes() > 900 * 1024**2
    finally:
        reset_sandbox_limits(tokens)


def test_publication_fetches_only_the_inputs_it_cites(monkeypatch, tmp_path):
    """A resumed run's collected logs are in the box, not on the fresh host."""
    from inspect_audit import _report

    root = _remote_root(tmp_path)
    (root / "inputs/jobs/pilot").mkdir(parents=True)
    (root / "inputs/jobs/pilot/have.eval").write_text("x")
    report = root / "work/report"
    report.mkdir(parents=True)
    evidence = [
        {"path": "/inputs/jobs/pilot/have.eval", "location": "s1"},
        {"path": "/inputs/jobs/pilot/missing.eval", "location": "s2"},
        {"path": "figure.png", "location": "fig"},
    ]
    (report / "findings.json").write_text(json.dumps([{"id": "F1", "evidence": evidence}]))
    assert _report.cited_inputs(root) == [
        "/inputs/jobs/pilot/have.eval",
        "/inputs/jobs/pilot/missing.eval",
    ]

    read: list[str] = []

    class Box:
        async def read_file(self, path, text=True):
            read.append(path)
            return b"restored"

    monkeypatch.setattr(workspace, "sandbox", Box)
    asyncio.run(workspace.fetch_inputs_files(root, _report.cited_inputs(root)))
    assert read == ["/inputs/jobs/pilot/missing.eval"]
    assert (root / "inputs/jobs/pilot/missing.eval").read_bytes() == b"restored"


@pytest.mark.parametrize(
    ("key", "acted_on"),
    [
        # hawk 3.6.0 job_status_updated processors: what Hawk acts on when it appears
        ("evals/chess-audit-sol6-20260925/artifacts/s/published/p/_inputs/logs/gemini.eval", True),
        ("evals/run/artifacts/s/state/jobs/downloads/pilot/run.eval", True),
        ("evals/run/.models.json", True),
        ("evals/run/eval-set.json", True),
        ("evals/run/2026-09-28_task.fast.eval", False),
        ("evals/run/artifacts/s/published/p/report.pdf", False),
        ("evals/run/artifacts/s/state/jobs.json", False),
        ("scans/run/scanner/_summary.json", True),
        ("scans/run/scanner/results.parquet", True),
        ("scans/run/scanner/notes.md", False),
        ("audit-inputs/anything.eval", False),
    ],
)
def test_the_upload_guard_matches_what_hawk_acts_on(key, acted_on):
    assert (workspace.hawk_would_process(key) is not None) == acted_on


def test_an_upload_hawk_would_act_on_writes_nothing(tmp_path, monkeypatch):
    log = tmp_path / "copy.eval"
    log.write_bytes(b"x")
    report = tmp_path / "report.pdf"
    report.write_bytes(b"%PDF")
    written = []
    import fsspec

    class Recorder:
        def makedirs(self, *args, **kwargs):
            pass

        def put_file(self, source, target):
            written.append(target)

    monkeypatch.setattr(
        fsspec.core, "url_to_fs", lambda url: (Recorder(), url.removeprefix("s3://"))
    )
    with pytest.raises(ValueError, match=r"imports every \.eval"):
        workspace._upload(
            {"report.pdf": report, "_inputs/logs/copy.eval": log},
            "s3://bucket/evals/run/artifacts/sample-epoch1/published/p",
        )
    assert written == []  # checked before the first object, not after


def test_a_k8s_box_is_a_pod_with_nothing_mounted(monkeypatch, tmp_path):
    """The box is declared; nothing about where this process runs decides it."""
    monkeypatch.setenv("HAWK_JOB_ID", "set-but-irrelevant")
    assert investigate(repo="https://example.org/b.git", output_dir=str(tmp_path)).sandbox.type == (
        "docker"
    )
    task = investigate(
        repo="https://example.org/benchmark.git",
        revision="abc123",
        output_dir=str(tmp_path),
        box="k8s",
        hawk_api_url="https://hawk.example",
        artifact_dir="s3://bucket/test-investigation/artifacts",
    )
    assert task.sandbox.type == "k8s"
    values = yaml.safe_load(Path(task.sandbox.config).read_text())
    assert "volumes" not in values["services"]["default"]
    assert values["automountServiceAccountToken"] is False
    root = Path(task.metadata["investigation_dir"])
    assert json.loads((root / "artifacts.json").read_text())["artifact_dir"].startswith("s3://")


def test_a_docker_box_is_a_container_with_nothing_mounted(tmp_path):
    task = investigate(repo="https://example.org/benchmark.git", output_dir=str(tmp_path))
    assert task.sandbox.type == "docker"
    compose = yaml.safe_load(Path(task.sandbox.config).read_text())
    assert "volumes" not in compose["services"]["default"]


def test_the_artifact_destination_is_keyed_by_sample_and_epoch(tmp_path):
    """Inspect gives a resumed sample a new uuid; its saved artifacts must not move."""
    root = _remote_root(tmp_path)
    settings = json.loads((root / "artifacts.json").read_text())
    settings["sample_key"] = "investigation-epoch1"
    (root / "artifacts.json").write_text(json.dumps(settings))
    assert workspace._destination(root, "published").endswith("/investigation-epoch1/published")


def test_cited_evidence_only_in_the_box_is_recovered_before_validation(monkeypatch, tmp_path):
    """After a resume on a fresh host the restored box has inputs this host lacks;
    checking the report must fetch what it cites before looking for it."""
    from inspect_audit import _report

    report = tmp_path / "work/report"
    report.mkdir(parents=True)
    (report / "findings.json").write_text(
        json.dumps(
            [
                {
                    "id": "F1",
                    "section": "grading",
                    "claim": "c",
                    "status": "supported",
                    "origin": "historical",
                    "reproduce": "read",
                    "limitations": "",
                    "evidence": [{"path": "/inputs/jobs/bench/table.csv", "location": "row 1"}],
                }
            ]
        )
    )
    fetched = []

    async def pull(root):
        return []

    async def push(root):
        pass

    async def fetch(root, paths):
        # what the box holds: the cited file appears on the host once fetched
        for path in paths:
            host = root / "inputs" / Path(path).relative_to("/inputs")
            host.parent.mkdir(parents=True, exist_ok=True)
            host.write_text("id,score\n")
            fetched.append(path)

    monkeypatch.setattr(workspace, "pull", pull)
    monkeypatch.setattr(workspace, "push_assessments", push)
    monkeypatch.setattr(workspace, "fetch_inputs_files", fetch)
    result = asyncio.run(_report.check_report(str(tmp_path))())
    assert "validated" in result
    assert fetched == ["/inputs/jobs/bench/table.csv"]
