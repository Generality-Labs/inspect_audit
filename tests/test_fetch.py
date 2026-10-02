"""Log sources an audit can be pointed at, resolved before anything reads them."""

import io
import json
import urllib.request
from pathlib import Path

from inspect_audit._registry import fetch_logs


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


def test_paths_and_native_urls_pass_through() -> None:
    assert fetch_logs("logs/") == "logs/"
    assert fetch_logs("s3://bucket/prefix") == "s3://bucket/prefix"


def test_exact_hawk_files_exclude_partial_runs(monkeypatch):
    monkeypatch.setenv("HAWK_API_URL", "https://hawk.test")
    monkeypatch.setenv("HAWK_ACCESS_TOKEN", "test-token")
    downloaded = []

    def open_url(request, timeout=None):
        if isinstance(request, str):
            downloaded.append(request)
            return io.BytesIO(b"selected log")
        if request.full_url.endswith("logs?log_dir=run"):
            return io.BytesIO(
                json.dumps(
                    {"files": [{"name": "run/complete.eval"}, {"name": "run/partial.eval"}]}
                ).encode()
            )
        payload = json.loads(request.data)
        assert payload["logs"] == ["run/complete.eval"]
        return io.BytesIO(
            json.dumps(
                {"urls": [{"filename": "complete.eval", "url": "https://download.test/complete"}]}
            ).encode()
        )

    monkeypatch.setattr(urllib.request, "urlopen", open_url)
    result = Path(fetch_logs("hawk:run/complete.eval"))
    assert (result / "complete.eval").read_bytes() == b"selected log"
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


def test_signed_http_log_urls_are_downloaded_before_inspect_parses_paths(monkeypatch):
    import fsspec

    sources = [
        "https://read.test/a.eval?signature=first",
        "https://read.test/a.eval?signature=second",
    ]
    opened = []

    def open_remote(source, mode):
        assert mode == "rb"
        opened.append(source)
        return io.BytesIO(source.encode())

    monkeypatch.setattr(fsspec, "open", open_remote)
    result = Path(fetch_logs(sources))
    assert sorted(p.read_text() for p in result.glob("*.eval")) == sorted(sources)
    assert opened == sources


def test_a_list_of_s3_files_uses_remote_reads(monkeypatch):
    import fsspec

    monkeypatch.setattr(fsspec, "open", lambda source, mode: io.BytesIO(b"archived log"))
    result = Path(fetch_logs(["s3://supplied/source.eval"]))
    assert (result / "0_source.eval").read_bytes() == b"archived log"
