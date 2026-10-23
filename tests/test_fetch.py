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


def test_multiple_log_sources_preserve_same_named_files(tmp_path):
    paths = []
    for n in range(2):
        folder = tmp_path / str(n)
        folder.mkdir()
        source = folder / "same.eval"
        source.write_text(str(n))
        paths.append(str(source))
    result = Path(fetch_logs(paths))
    assert sorted(p.read_text() for p in result.glob("*.eval")) == ["0", "1"]


def test_directory_sources_accept_inspect_file_uris(tmp_path):
    from test_helpers.logs import run_fixture_eval

    folder = tmp_path / "logs with spaces"
    run_fixture_eval(str(folder))
    copied = Path(fetch_logs([str(folder)]))
    originals = list(folder.glob("*.eval"))
    assert len(originals) == 1
    assert (copied / f"0_{originals[0].name}").read_bytes() == originals[0].read_bytes()


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
    """Hawk's downloader reads its address from settings; ours must reach it, and a run
    that wrote no logs is recorded as such rather than crashing collection."""
    import asyncio
    import os

    import click
    import hawk.cli.download

    from inspect_audit.dispatch.hawk import download

    seen = []

    async def empty(eval_set, token, out_dir, eval_file=None):
        seen.append(os.environ.get("HAWK_API_URL"))
        raise click.ClickException(f"No log files found in eval set: {eval_set}")

    monkeypatch.setenv("HAWK_API_URL", "https://elsewhere.example")
    monkeypatch.setattr(hawk.cli.download, "download_eval", empty)
    assert asyncio.run(download("run", "t", tmp_path, "https://ours.example")) == []
    assert seen == ["https://ours.example"]
    assert os.environ["HAWK_API_URL"] == "https://elsewhere.example"  # restored
