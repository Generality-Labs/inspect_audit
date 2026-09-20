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

        def __enter__(self):  # noqa: ANN204
            return self

        def __exit__(self, *exc):  # noqa: ANN002
            return False

    def urlopen(url, timeout=None):  # noqa: ANN001
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
            return io.BytesIO(json.dumps({"files": [{"name": "run/complete.eval"}, {"name": "run/partial.eval"}]}).encode())
        payload = json.loads(request.data)
        assert payload["logs"] == ["run/complete.eval"]
        return io.BytesIO(json.dumps({"urls": [{"filename": "complete.eval", "url": "https://download.test/complete"}]}).encode())

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
