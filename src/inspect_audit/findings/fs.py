"""One way to read and write a store, whether it is a directory, S3, R2 or an fsspec memory filesystem.

Keys are relative to the store root and use `/`. Listings are sorted, so no caller's output
depends on the order a backend enumerates objects.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

from fsspec import AbstractFileSystem
from fsspec.core import url_to_fs


class StoreFS:
    def __init__(self, fs: AbstractFileSystem, root: str) -> None:
        self.fs = fs
        self.root = root.rstrip("/")

    @classmethod
    def from_locator(cls, locator: str | Path) -> StoreFS:
        text = str(locator)
        if "://" not in text:
            text = Path(text).expanduser().resolve().as_posix()
        fs, root = url_to_fs(text)
        return cls(fs, str(root))

    @property
    def protocol(self) -> str:
        protocol = self.fs.protocol
        return protocol if isinstance(protocol, str) else protocol[0]

    @property
    def locator(self) -> str:
        return self.root if self.protocol == "file" else f"{self.protocol}://{self.root}"

    def key(self, *parts: str) -> str:
        return "/".join(p.strip("/") for p in parts if p)

    def _full(self, key: str) -> str:
        return f"{self.root}/{key}" if key else self.root

    def _rel(self, full: str) -> str:
        text = str(full)
        for candidate in (text, text.lstrip("/")):
            for root in (self.root, self.root.lstrip("/")):
                if candidate.startswith(root + "/"):
                    return candidate[len(root) + 1 :]
        return text

    def exists(self, key: str) -> bool:
        return bool(self.fs.exists(self._full(key)))

    def is_file(self, key: str) -> bool:
        return bool(self.fs.isfile(self._full(key)))

    def read_text(self, key: str) -> str:
        return self.read_bytes(key).decode("utf-8")

    def read_bytes(self, key: str) -> bytes:
        with self.fs.open(self._full(key), "rb") as handle:
            return bytes(cast("Any", handle).read())

    def write_text(self, key: str, text: str) -> str:
        return self.write_bytes(key, text.encode("utf-8"))

    def write_bytes(self, key: str, data: bytes) -> str:
        full = self._full(key)
        self.fs.makedirs(full.rsplit("/", 1)[0], exist_ok=True)
        with self.fs.open(full, "wb") as handle:
            cast("Any", handle).write(data)
        return key

    def ls(self, prefix: str) -> list[str]:
        full = self._full(prefix)
        if not self.fs.exists(full):
            return []
        entries = cast("list[str]", self.fs.ls(full, detail=False))
        return sorted(self._rel(p) for p in entries)

    def glob(self, pattern: str) -> list[str]:
        matches = cast("list[str]", self.fs.glob(self._full(pattern)))
        return sorted(self._rel(p) for p in matches)

    def local_path(self, key: str) -> Path | None:
        return Path(self._full(key)) if self.protocol == "file" else None


def as_store_fs(value: StoreFS | str | Path) -> StoreFS:
    return value if isinstance(value, StoreFS) else StoreFS.from_locator(value)
