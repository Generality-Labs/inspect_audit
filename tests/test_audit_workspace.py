"""The auditor's box, and the host's trusted copy of what is in it."""

import asyncio
import json
from pathlib import Path

import pytest
import yaml

from inspect_audit import _audit_workspace as workspace
from inspect_audit._audit import audit


def _remote_root(tmp_path, base="memory://investigation-test/artifacts"):
    workspace.configure(tmp_path, "image@sha256:abc")
    (tmp_path / "artifacts.json").write_text(
        json.dumps({"artifact_dir": base, "sample_uuid": "sample"})
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
        assert destination == f"memory://investigation-test/artifacts/sample/published/{version}"
        with fsspec.open(destination + "/nested/evidence.json") as file:
            assert file.read() == b"{}"
    fs = fsspec.filesystem("memory")
    assert not fs.exists("/investigation-test/artifacts/sample/published/v2/fig-old.png")


@pytest.mark.parametrize(
    ("key", "acted_on"),
    [
        # hawk 3.6.0 job_status_updated processors: what Hawk acts on when it appears
        (
            "evals/chess-investigation-sol6-20260925/artifacts/s/published/p/_inputs/logs/gemini.eval",
            True,
        ),
        ("evals/run/artifacts/s/state/jobs/downloads/pilot/run.eval", True),
        ("evals/run/.models.json", True),
        ("evals/run/eval-set.json", True),
        ("evals/run/2026-09-28_task.fast.eval", False),
        ("evals/run/artifacts/s/published/p/report.pdf", False),
        ("evals/run/artifacts/s/state/jobs.json", False),
        ("scans/run/scanner/_summary.json", True),
        ("scans/run/scanner/results.parquet", True),
        ("scans/run/scanner/notes.md", False),
        ("investigation-inputs/anything.eval", False),
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
    assert audit(repo="https://example.org/b.git", output_dir=str(tmp_path)).sandbox.type == (
        "docker"
    )
    task = audit(
        repo="https://example.org/benchmark.git",
        revision="abc123",
        output_dir=str(tmp_path),
        box="k8s",
        hawk_api_url="https://hawk.example",
        artifact_dir="s3://bucket/test-audit/artifacts",
    )
    assert task.sandbox.type == "k8s"
    values = yaml.safe_load(Path(task.sandbox.config).read_text())
    assert "volumes" not in values["services"]["default"]
    assert values["automountServiceAccountToken"] is False
    root = Path(task.metadata["audit_dir"])
    assert json.loads((root / "artifacts.json").read_text())["artifact_dir"].startswith("s3://")


def test_a_docker_box_is_a_container_with_nothing_mounted(tmp_path):
    task = audit(repo="https://example.org/benchmark.git", output_dir=str(tmp_path))
    assert task.sandbox.type == "docker"
    compose = yaml.safe_load(Path(task.sandbox.config).read_text())
    assert "volumes" not in compose["services"]["default"]


def test_artifacts_land_where_hawks_viewer_looks_for_the_sample(tmp_path):
    """Hawk serves a sample's artifacts from artifacts/<sample uuid>/."""
    root = _remote_root(tmp_path)
    settings = json.loads((root / "artifacts.json").read_text())
    settings["sample_uuid"] = "9uE3myJMUv4H9bs2Rw4nVj"
    (root / "artifacts.json").write_text(json.dumps(settings))
    assert workspace._destination(root, "published").endswith(
        "/artifacts/9uE3myJMUv4H9bs2Rw4nVj/published"
    )
