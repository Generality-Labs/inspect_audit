"""Log sources an investigation can be pointed at, resolved before anything reads them.

`hawk:` sources are fetched from a Hawk API on localhost (the `hawk` fixture) through
Hawk's own client; declared inputs come from the environment a Hawk runner sets.
"""

import json
from pathlib import Path
from typing import Any

import pytest
from test_helpers.logs import run_fixture_eval

from inspect_audit import _log_sources
from inspect_audit._log_sources import fetch_logs, log_files


@pytest.fixture(autouse=True)
def fresh_fetches(monkeypatch: pytest.MonkeyPatch) -> None:
    # the per-process memo of what was fetched: each test starts a new process's worth
    monkeypatch.setattr(_log_sources, "_FETCHED", {})
    monkeypatch.delenv("HAWK_TRANSCRIPT_SOURCES", raising=False)


def _logs_on_hawk(hawk: Any, tmp_path: Path, run_id: str, names: list[str]) -> list[Path]:
    """A finished run on the fake Hawk holding real logs under these file names."""
    from test_helpers.fake_hawk import FakeJob

    made = Path(run_fixture_eval(str(tmp_path / "made")))
    logs = []
    for name in names:
        copy = tmp_path / "made" / name
        copy.write_bytes(made.read_bytes())
        logs.append(copy)
    hawk.jobs[run_id] = FakeJob(config={}, body={}, status="complete", logs=logs)
    return logs


def test_paths_pass_through() -> None:
    assert fetch_logs("logs/") == "logs/"


def test_a_log_url_is_downloaded_to_a_local_file(tmp_path: Path) -> None:
    """Staging reads local paths: a URL left as-is reads as `s3:/...`, which does not exist."""
    import functools
    import http.server
    import threading

    (tmp_path / "run.eval").write_bytes(b"log bytes")
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(tmp_path))
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        local = Path(fetch_logs(f"http://127.0.0.1:{server.server_port}/run.eval"))
    finally:
        server.shutdown()
    assert local.is_file() and local.read_bytes() == b"log bytes"


def _written_on_hawk(hawk: Any, tmp_path: Path, run_id: str, names: list[str], **kw: Any) -> list[Path]:
    """`_logs_on_hawk` with logs written by Inspect's writer (no eval run, no network)."""
    from test_helpers.fake_hawk import FakeJob
    from test_helpers.logs import write_fixture_log

    logs = [Path(write_fixture_log(str(tmp_path / name), **kw)) for name in names]
    hawk.jobs[run_id] = FakeJob(config={}, body={}, status="complete", logs=logs)
    return logs


@pytest.fixture
def hawksrc(hawk: Any, monkeypatch: pytest.MonkeyPatch) -> Any:
    """Hawk's hawksrc:// filesystem, talking to the fake Hawk."""
    import fsspec

    monkeypatch.setenv("HAWK_API_URL", hawk.url)
    for name in ("HAWK_RUNNER_RESTRICTED_API_URL", "HAWK_RUNNER_API_URL"):
        monkeypatch.delenv(name, raising=False)
    fsspec.get_filesystem_class("hawksrc").clear_instance_cache()
    yield hawk
    fsspec.get_filesystem_class("hawksrc").clear_instance_cache()


def test_a_hawk_log_is_read_in_place_by_range_never_downloaded(tmp_path: Path, hawksrc: Any) -> None:
    """`hawk:<set>/<file>.eval` (FRICTION 5) names only that file, read where it lives:
    a job reads the header, the summaries and the sample it needs, not the whole log."""
    from inspect_ai.log import read_eval_log, read_eval_log_sample, read_eval_log_sample_summaries

    complete, _partial = _written_on_hawk(
        hawksrc, tmp_path, "run", ["complete.eval", "partial.eval"], padding={"c": 16_000_000}
    )
    [found] = log_files(["hawk:run/complete.eval"])
    assert found == "hawksrc://evals/run/complete.eval"
    assert sorted(log_files(["hawk:run"])) == [
        "hawksrc://evals/run/complete.eval",
        "hawksrc://evals/run/partial.eval",
    ]
    assert read_eval_log(found, header_only=True).eval.task == "fixture"
    assert len(read_eval_log_sample_summaries(found)) == 6
    assert read_eval_log_sample(found, id="a", epoch=2) == read_eval_log_sample(
        str(complete), id="a", epoch=2
    )
    assert 0 < hawksrc.bytes_served < complete.stat().st_size / 4
    assert set(hawksrc.tokens_seen) == {"Bearer test-token"}
    with pytest.raises(ValueError, match="exactly one"):
        log_files(["hawk:run/missing.eval"])


def test_a_remote_log_is_fetched_once_per_process(
    tmp_path: Path, hawk: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A hundred job items naming one Hawk log must not download it a hundred times."""
    _logs_on_hawk(hawk, tmp_path, "run", ["a.eval"])
    monkeypatch.setenv("HAWK_API_URL", hawk.url)
    first = fetch_logs("hawk:run")
    asked = len(hawk.tokens_seen)
    assert [fetch_logs("hawk:run") for _ in range(3)] == [first] * 3
    assert len(hawk.tokens_seen) == asked, "Hawk was asked again"


def test_hawk_downloads_use_the_given_address_and_an_empty_set_is_no_logs(
    tmp_path: Path, hawk: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Downloads go to the address and token this job was given (Hawk 4's CLI
    downloader refuses supplied tokens, FRICTION 70), and a run that wrote no logs is
    recorded as such rather than crashing collection."""
    import asyncio

    from test_helpers.fake_hawk import FakeJob

    from inspect_audit.dispatch.hawk import download

    hawk.jobs["run"] = FakeJob(config={}, body={}, status="failed")
    monkeypatch.setenv("HAWK_API_URL", "http://127.0.0.1:9/elsewhere")
    assert asyncio.run(download("run", "t", tmp_path, hawk.url)) == []
    assert hawk.tokens_seen == ["Bearer t"]


def test_a_declared_hawk_input_is_read_in_place_and_others_downloaded(
    tmp_path: Path, hawk: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Declaring inputs needs restricted credentials, which cannot launch jobs, so an audit
    on Hawk reads its own runs' logs with its own token; a declared input is used at its
    declared location (FRICTION 83). Declared inputs are what a Hawk runner puts in its
    environment. `fetch_logs` (an auditor's `download` log access) downloads the rest."""
    _logs_on_hawk(hawk, tmp_path, "mine", ["a.eval"])
    monkeypatch.setenv("HAWK_API_URL", hawk.url)
    monkeypatch.setenv(
        "HAWK_TRANSCRIPT_SOURCES",
        json.dumps([{"eval_set_id": "other", "location": "hawksrc://other"}]),
    )
    assert fetch_logs("hawk:other") == "hawksrc://other"
    downloaded = Path(fetch_logs("hawk:mine"))
    assert [p.name for p in (downloaded / "mine").glob("*.eval")] == ["a.eval"]


def test_log_files_read_a_declared_hawk_input_at_its_location(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from test_helpers.logs import write_fixture_log

    folder = tmp_path / "declared"
    folder.mkdir()
    write_fixture_log(str(folder / "run.eval"))
    monkeypatch.setattr(_log_sources, "declared_transcripts", lambda: {"other": str(folder)})
    assert log_files(["hawk:other"]) == [str(folder / "run.eval")]


def test_directory_sources_accept_inspect_file_uris(tmp_path: Path) -> None:
    folder = tmp_path / "logs with spaces"
    run_fixture_eval(str(folder))
    originals = list(folder.glob("*.eval"))
    assert len(originals) == 1
    assert log_files([str(folder)]) == [str(originals[0])]


# ------------------------------------------------------------------ findings bundles


def _published(tmp_path: Path) -> tuple[Path, Path]:
    """A sample's artifact tree as an audit leaves it: published/<id>/ with its bundle."""
    import shutil

    from test_epoch import PROSE, bundle, findings, phase1, ten

    made = tmp_path / "made"
    made.mkdir()
    source = bundle(made, findings(ten(1), phase1()))
    artifacts = tmp_path / "artifacts"
    shutil.copytree(source, artifacts / "published/abc/findings")
    (artifacts / "published/abc/prose.json").write_text(json.dumps(PROSE))
    return artifacts, source


def test_a_findings_bundle_is_fetched_from_a_hawk_samples_artifacts(
    tmp_path: Path, hawk: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`publish` on a Hawk runner reads a finished audit's published folder through
    Hawk's sample-artifact API with the runner's own token, and finds its bundle."""
    from inspect_audit._log_sources import fetch_bundle
    from inspect_audit._publishing import publish

    artifacts, source = _published(tmp_path)
    hawk.artifacts[("audit-run", "S1")] = artifacts
    monkeypatch.setenv("HAWK_API_URL", hawk.url)
    fetched = Path(fetch_bundle("hawk:audit-run/artifacts/S1/published/abc"))
    assert (fetched / "prose.json").exists()
    assert (fetched / "findings/findings.json").read_bytes() == (
        source / "findings.json"
    ).read_bytes()
    # the page renders again from the fetched folder, with the prose.json beside it
    from inspect_audit.publishers import epoch

    out = tmp_path / "again"
    assert epoch.main([str(fetched), "--out", str(out), "--full-page"]) == 0
    assert "## Summary" in (out / "epoch_review.md").read_text()
    task = publish(
        "hawk:audit-run/artifacts/S1/published/abc",
        publisher={"name": "epoch", "full_page": True},
        output_dir=str(tmp_path / "out"),
    )
    assert task.metadata is not None
    root = Path(task.metadata["audit_dir"])
    assert (root / "inputs/findings/findings.json").read_bytes() == (
        source / "findings.json"
    ).read_bytes()
    assert task.metadata["publisher"] == {"name": "epoch", "full_page": True}
    # the publisher's skill reads the full page's briefs, staged as an audit stages them
    staged = (root / "work/analysis/epoch/methodology.md").read_text()
    assert "required for Verified" in staged and "not rendered" not in staged
    # off Hawk, with no artifact_dir, the page stays on this host
    assert not (root / "artifacts.json").exists()
    kept = publish(
        str(source), publisher="epoch", output_dir=str(tmp_path / "kept"), artifact_dir="s3://b/x"
    )
    assert kept.metadata is not None
    settings = json.loads((Path(kept.metadata["audit_dir"]) / "artifacts.json").read_text())
    assert settings == {"artifact_dir": "s3://b/x"}
    assert set(hawk.tokens_seen) == {"Bearer test-token"}
    with pytest.raises(ValueError, match="artifacts/<sample uuid>"):
        fetch_bundle("hawk:audit-run/S1/published/abc")


def test_a_findings_bundle_is_copied_from_a_bucket(tmp_path: Path) -> None:
    """An s3:// or gs:// folder is copied with fsspec (here its in-memory filesystem)."""
    import fsspec

    from inspect_audit._log_sources import fetch_bundle

    artifacts, source = _published(tmp_path)
    fs = fsspec.filesystem("memory")
    for path in artifacts.rglob("*"):
        if path.is_file():
            fs.pipe(f"/bucket/run/{path.relative_to(artifacts).as_posix()}", path.read_bytes())
    fetched = Path(fetch_bundle("memory://bucket/run/published/abc/findings/"))
    assert sorted(p.name for p in fetched.rglob("*") if p.is_file()) == sorted(
        p.name for p in source.rglob("*") if p.is_file()
    )
    assert fetch_bundle(str(source)) == str(source)
    with pytest.raises(ValueError, match="is a folder"):
        fetch_bundle("https://example.com/bundle")
