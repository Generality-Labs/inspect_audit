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


def test_one_hawk_log_is_fetched_without_the_runs_others(
    tmp_path: Path, hawk: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`hawk:<set>/<file>.eval` ends in `.eval` but is not a local path (FRICTION 5), and
    only that file is downloaded, not the run's partial logs."""
    complete, _partial = _logs_on_hawk(hawk, tmp_path, "run", ["complete.eval", "partial.eval"])
    monkeypatch.setenv("HAWK_API_URL", hawk.url)
    [found] = log_files(["hawk:run/complete.eval"])
    assert Path(found).name == "complete.eval"
    assert Path(found).read_bytes() == complete.read_bytes()
    assert not list(Path(found).parent.glob("partial.eval"))
    assert set(hawk.tokens_seen) == {"Bearer test-token"}


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
    on Hawk reads its own runs' logs by download; a declared input is used in place
    (FRICTION 83). Declared inputs are what a Hawk runner puts in its environment."""
    _logs_on_hawk(hawk, tmp_path, "mine", ["a.eval"])
    monkeypatch.setenv("HAWK_API_URL", hawk.url)
    monkeypatch.setenv(
        "HAWK_TRANSCRIPT_SOURCES",
        json.dumps([{"eval_set_id": "other", "location": "hawksrc://other"}]),
    )
    assert fetch_logs("hawk:other") == "hawksrc://other"
    downloaded = Path(fetch_logs("hawk:mine"))
    assert [p.name for p in (downloaded / "mine").glob("*.eval")] == ["a.eval"]


def test_directory_sources_accept_inspect_file_uris(tmp_path: Path) -> None:
    folder = tmp_path / "logs with spaces"
    run_fixture_eval(str(folder))
    originals = list(folder.glob("*.eval"))
    assert len(originals) == 1
    assert log_files([str(folder)]) == [str(originals[0])]
