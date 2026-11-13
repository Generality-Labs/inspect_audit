"""Log sources an investigation can be pointed at, resolved before anything reads them."""

import urllib.request
from pathlib import Path

from inspect_audit._log_sources import fetch_logs


def _serve(monkeypatch, pages: dict[str, bytes]) -> None:
    class Response:
        def __init__(self, body: bytes) -> None:
            self.body = body

        def read(self) -> bytes:
            return self.body

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def urlopen(url, timeout=None):
        if url not in pages:
            raise ValueError(f"unexpected fetch {url!r}")
        return Response(pages[url])

    monkeypatch.setattr(urllib.request, "urlopen", urlopen)


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


def test_exact_hawk_files_exclude_partial_runs(monkeypatch):
    import hawk.cli.download
    import hawk.client

    monkeypatch.setenv("HAWK_API_URL", "https://hawk.test")
    monkeypatch.setenv("HAWK_ACCESS_TOKEN", "test-token")
    presigned, downloaded = [], []

    class Client:
        def __init__(self, token, api_url=None):
            assert token == "test-token"

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def get_log_files(self, eval_set_id):
            assert eval_set_id == "run"
            return [{"name": "run/complete.eval"}, {"name": "run/partial.eval"}]

        async def get_download_urls(self, names):
            presigned.extend(names)
            yield "https://download.test/complete", "complete.eval"

    async def download_file(url, dest):
        downloaded.append(url)
        dest.write_bytes(b"selected log")

    monkeypatch.setattr(hawk.client, "HawkClient", Client)
    monkeypatch.setattr(hawk.cli.download, "download_file", download_file)
    monkeypatch.setattr(hawk.cli.download, "_assert_server_supports_batch_download", lambda: None)
    result = Path(fetch_logs("hawk:run/complete.eval")) / "run"
    assert (result / "complete.eval").read_bytes() == b"selected log"
    assert presigned == ["run/complete.eval"]
    assert downloaded == ["https://download.test/complete"]


def test_directory_sources_accept_inspect_file_uris(tmp_path):
    from test_helpers.logs import run_fixture_eval

    folder = tmp_path / "logs with spaces"
    run_fixture_eval(str(folder))
    from inspect_audit._log_sources import log_files

    originals = list(folder.glob("*.eval"))
    assert len(originals) == 1
    assert log_files([str(folder)]) == [str(originals[0])]


def test_a_remote_log_is_fetched_once_per_process(tmp_path, monkeypatch):
    """A hundred job items naming one Hawk log must not download it a hundred times."""
    from inspect_audit import _log_sources

    calls = []

    def fetch(address):
        calls.append(address)
        folder = tmp_path / f"fetch{len(calls)}"
        folder.mkdir()
        return str(folder)

    monkeypatch.setattr(_log_sources, "fetch_eval_sets", fetch)
    monkeypatch.setattr(_log_sources, "declared_transcripts", lambda: None)
    monkeypatch.setattr(_log_sources, "_FETCHED", {})
    first = _log_sources.fetch_logs("hawk:run")
    assert [_log_sources.fetch_logs("hawk:run") for _ in range(3)] == [first] * 3
    assert calls == ["run"]


def test_hawk_downloads_use_the_given_address_and_an_empty_set_is_no_logs(tmp_path, monkeypatch):
    """Downloads go to the address and token this job was given (Hawk 4's CLI
    downloader refuses supplied tokens, FRICTION 70), and a run that wrote no logs is
    recorded as such rather than crashing collection."""
    import asyncio

    import hawk.client

    from inspect_audit.dispatch.hawk import download

    seen = []

    class Client:
        def __init__(self, token, api_url=None):
            seen.append((token, api_url))

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def get_log_files(self, eval_set_id):
            return []

    monkeypatch.setenv("HAWK_API_URL", "https://elsewhere.example")
    monkeypatch.setattr(hawk.client, "HawkClient", Client)
    assert asyncio.run(download("run", "t", tmp_path, "https://ours.example")) == []
    assert seen == [("t", "https://ours.example")]


def test_a_single_hawk_log_address_is_fetched_not_taken_as_a_local_file(tmp_path, monkeypatch):
    """FRICTION 5: `hawk:<set>/<file>.eval` ends in `.eval`, and was returned unchanged
    as if it were a local path, so the log was never downloaded."""
    from test_helpers.logs import run_fixture_eval

    from inspect_audit import _log_sources

    log = Path(run_fixture_eval(str(tmp_path / "fetched")))
    asked = []

    def fetch(entry):
        asked.append(entry)
        return str(log.parent)

    monkeypatch.setattr(_log_sources, "fetch_logs", fetch)
    assert _log_sources.log_files(["hawk:run/a.eval"]) == [str(log)]
    assert asked == ["hawk:run/a.eval"]


def test_an_undeclared_hawk_log_is_downloaded_with_the_runners_token(monkeypatch):
    """Declaring inputs needs restricted credentials, which cannot launch jobs, so an audit
    on Hawk reads its logs by download; a declared input is still used in place (FRICTION 83)."""
    from inspect_audit import _log_sources

    _log_sources._FETCHED.clear()
    fetched = []
    monkeypatch.setattr(_log_sources, "declared_transcripts", lambda: {"other": "hawksrc://other"})
    monkeypatch.setattr(
        _log_sources, "fetch_eval_sets", lambda ids: fetched.append(ids) or "/tmp/x"
    )
    assert _log_sources.fetch_logs("hawk:mine") == "/tmp/x"
    assert fetched == ["mine"]
    assert _log_sources.fetch_logs("hawk:other") == "hawksrc://other"
