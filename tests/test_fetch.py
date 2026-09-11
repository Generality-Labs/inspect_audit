"""Log sources an audit can be pointed at, resolved before anything reads them."""

import urllib.request

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
