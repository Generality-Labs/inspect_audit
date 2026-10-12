"""One way to read and write a store, whether it is a directory, S3, R2 or an fsspec memory filesystem.

Keys are relative to the store root and use `/`. Listings are sorted, so no caller's output
depends on the order a backend enumerates objects.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
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
        options: dict[str, Any] = {}
        if "://" not in text:
            text = Path(text).expanduser().resolve().as_posix()
        elif text.startswith(("s3://", "s3a://")):
            # botocore's default pool of 10 connections serialises a batch read of hundreds of runs
            options["config_kwargs"] = {"max_pool_connections": 64}
        fs, root = url_to_fs(text, **options)
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

    def read_texts(self, keys: Sequence[str]) -> dict[str, str]:
        """Several objects in one call, which object stores fetch concurrently. Missing keys raise."""
        if not keys:
            return {}
        fulls = {self._full(key): key for key in keys}
        fetched = cast("dict[str, bytes]", self.fs.cat(list(fulls)))
        by_key = {self._rel(path): data for path, data in fetched.items()}
        return {key: by_key[key].decode("utf-8") for key in keys}

    def write_texts(self, items: Mapping[str, str]) -> list[str]:
        """Several objects in one call, which object stores write concurrently; returns the keys, sorted."""
        return self.write_many({key: text.encode("utf-8") for key, text in items.items()})

    def write_text(self, key: str, text: str) -> str:
        return self.write_bytes(key, text.encode("utf-8"))

    def write_bytes(self, key: str, data: bytes) -> str:
        full = self._full(key)
        if self.protocol == "file":  # object stores have no directories to create
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
        """Keys matching `pattern`, where `*` and `?` stay within one path segment; sorted.

        On an object store this is one recursive listing matched locally: expanding a leading
        wildcard prefix by prefix costs one round trip per eval, which on R2 took minutes.
        """
        if self.protocol == "file":
            matches = cast("list[str]", self.fs.glob(self._full(pattern)))
            return sorted(self._rel(p) for p in matches)
        regex = _segment_regex(pattern)
        listed = cast("list[str]", self.fs.find(self.root))
        return sorted(key for key in (self._rel(p) for p in listed) if regex.fullmatch(key))

    def put_tree(self, local_dir: Path, prefix: str) -> list[str]:
        """Upload every file under `local_dir` to `prefix/<relative path>` in one batch; returns the keys, sorted."""
        files = {
            self.key(prefix, path.relative_to(local_dir).as_posix()): path.read_bytes()
            for path in sorted(p for p in local_dir.rglob("*") if p.is_file())
        }
        return self.write_many(files)

    def write_many(self, items: Mapping[str, bytes]) -> list[str]:
        """Several objects in one call, which object stores write concurrently; returns the keys, sorted."""
        if not items:
            return []
        fulls = {self._full(key): data for key, data in items.items()}
        if self.protocol == "file":
            for parent in {full.rsplit("/", 1)[0] for full in fulls}:
                self.fs.makedirs(parent, exist_ok=True)
        self.fs.pipe(fulls)
        return sorted(items)

    def local_path(self, key: str) -> Path | None:
        return Path(self._full(key)) if self.protocol == "file" else None


def _segment_regex(pattern: str) -> re.Pattern[str]:
    """A glob pattern as a regex whose wildcards never cross a `/`."""
    parts: list[str] = []
    for char in pattern:
        if char == "*":
            parts.append("[^/]*")
        elif char == "?":
            parts.append("[^/]")
        else:
            parts.append(re.escape(char))
    return re.compile("".join(parts))


def as_store_fs(value: StoreFS | str | Path) -> StoreFS:
    return value if isinstance(value, StoreFS) else StoreFS.from_locator(value)
