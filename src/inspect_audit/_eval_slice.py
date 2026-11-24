"""Copy samples out of an `.eval` log byte for byte, reading only what is copied.

An `.eval` log is a zip: `header.json`, `summaries.json`, `reductions.json` and one
`samples/<id>_epoch_<n>.json` entry per recorded attempt. A slice is a new zip with the
header, the summaries and reductions of the samples it keeps, and those samples' entries
copied as recorded: still compressed, attachments and all, never decompressed or
recompressed. The source is opened through fsspec, so a remote log (`hawksrc://`,
`s3://`) is read by range: its central directory, the small metadata entries and the
copied samples, never the whole file. Inspect reads a slice like any log.

A name recorded twice (a retried sample written again) resolves to its last entry,
as Inspect's readers resolve it; summaries are kept last-row-wins for the same reason.
The metadata entries are rewritten deflated, so the standard library alone can read
which attempts a slice holds.
"""

import json
import struct
import sys
import zlib
from collections.abc import Collection, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, BinaryIO
from zipfile import ZipFile, ZipInfo

if sys.version_info < (3, 14):  # zstd entries: Inspect's own dependency patches zipfile
    import zipfile_zstd  # type: ignore[import-not-found, import-untyped]  # noqa: F401

HEADER = "header.json"
SUMMARIES = "summaries.json"
REDUCTIONS = "reductions.json"
_CHUNK = 8 * 2**20

Key = tuple[str, int]
"""A recorded attempt in one log: (sample id as text, epoch)."""


def sample_entry(sample_id: str, epoch: int) -> str:
    """The zip entry Inspect writes a sample to."""
    return f"samples/{sample_id}_epoch_{epoch}.json"


@dataclass
class Copied:
    """What a slice or an addition copied."""

    samples: list[Key] = field(default_factory=list)
    bytes_read: int = 0
    """Compressed sample bytes read from the source (metadata reads not counted)."""


@contextmanager
def _source(path: str) -> Iterator[BinaryIO]:
    """A seekable handle on a local or remote log; remote reads fetch exactly what is read."""
    if "://" not in path or path.startswith("file://"):
        with open(path.removeprefix("file://"), "rb") as f:
            yield f
        return
    import fsspec  # pyright: ignore[reportMissingTypeStubs]

    # no read-ahead: the central directory and each copied entry are one ranged read each
    with fsspec.open(path, "rb", cache_type="none") as f:  # pyright: ignore[reportUnknownMemberType]
        yield f  # pyright: ignore[reportReturnType]


def _raw(f: BinaryIO, info: ZipInfo) -> Iterator[bytes]:
    """An entry's compressed bytes, as stored."""
    f.seek(info.header_offset)
    local = f.read(30)
    if local[:4] != b"PK\x03\x04":
        raise ValueError(f"{info.filename}: no local header at offset {info.header_offset}")
    name_length, extra_length = struct.unpack("<HH", local[26:30])
    f.seek(info.header_offset + 30 + name_length + extra_length)
    left = info.compress_size
    while left:
        chunk = f.read(min(_CHUNK, left))
        if not chunk:
            raise ValueError(f"{info.filename}: the log ends inside the entry")
        left -= len(chunk)
        yield chunk


class _Writer:
    """A zip64 writer that takes entries already compressed, or JSON to deflate."""

    def __init__(self, out: BinaryIO, entries: list[tuple[ZipInfo, int]] | None = None) -> None:
        self.out = out
        self.entries: list[tuple[ZipInfo, int]] = entries or []

    @staticmethod
    def _local(info: ZipInfo) -> bytes:
        name = info.filename.encode()
        extra = struct.pack("<HHQQ", 1, 16, info.file_size, info.compress_size)
        flags = 0x800 if not info.filename.isascii() else 0
        return (
            struct.pack(
                "<IHHHHHIIIHH",
                0x04034B50,
                45,
                flags,
                info.compress_type,
                0,
                0x21,  # 1980-01-01
                info.CRC,
                0xFFFFFFFF,
                0xFFFFFFFF,
                len(name),
                len(extra),
            )
            + name
            + extra
        )

    def add_raw(self, info: ZipInfo, chunks: Iterator[bytes]) -> int:
        """An entry copied as stored; its CRC and sizes are the source's own."""
        copy = ZipInfo(info.filename)
        copy.compress_type, copy.CRC = info.compress_type, info.CRC
        copy.compress_size, copy.file_size = info.compress_size, info.file_size
        offset = self.out.tell()
        self.out.write(self._local(copy))
        written = 0
        for chunk in chunks:
            self.out.write(chunk)
            written += len(chunk)
        if written != copy.compress_size:
            raise ValueError(f"{info.filename}: copied {written} of {copy.compress_size} bytes")
        self.entries.append((copy, offset))
        return written

    def add_json(self, name: str, value: Any) -> None:
        data = json.dumps(value).encode()
        compressor = zlib.compressobj(6, zlib.DEFLATED, -15)
        packed = compressor.compress(data) + compressor.flush()
        info = ZipInfo(name)
        info.compress_type, info.CRC = 8, zlib.crc32(data)
        info.compress_size, info.file_size = len(packed), len(data)
        self.add_raw(info, iter([packed]))

    def close(self) -> None:
        out = self.out
        start = out.tell()
        for info, offset in self.entries:
            name = info.filename.encode()
            extra = struct.pack("<HHQQQ", 1, 24, info.file_size, info.compress_size, offset)
            flags = 0x800 if not info.filename.isascii() else 0
            out.write(
                struct.pack(
                    "<IHHHHHHIIIHHHHHII",
                    0x02014B50,
                    45,
                    45,
                    flags,
                    info.compress_type,
                    0,
                    0x21,
                    info.CRC,
                    0xFFFFFFFF,
                    0xFFFFFFFF,
                    len(name),
                    len(extra),
                    0,
                    0,
                    0,
                    0,
                    0xFFFFFFFF,
                )
                + name
                + extra
            )
        size = out.tell() - start
        end64 = out.tell()
        n = len(self.entries)
        out.write(struct.pack("<IQHHIIQQQQ", 0x06064B50, 44, 45, 45, 0, 0, n, n, size, start))
        out.write(struct.pack("<IIQI", 0x07064B50, 0, end64, 1))
        out.write(
            struct.pack("<IHHHHIIH", 0x06054B50, 0, 0, 0xFFFF, 0xFFFF, 0xFFFFFFFF, 0xFFFFFFFF, 0)
        )
        out.truncate()


def _last_rows(rows: list[dict[str, Any]]) -> dict[Key, dict[str, Any]]:
    """Summary rows by attempt, the last row of a repeated attempt winning (as Inspect)."""
    return {(str(row["id"]), int(row["epoch"])): row for row in rows}


def recorded_samples(path: str) -> dict[Key, int]:
    """Every recorded sample entry in a log with its compressed size, read by range."""
    with _source(path) as f, ZipFile(f) as z:
        found: dict[Key, int] = {}
        for name, info in z.NameToInfo.items():
            if name.startswith("samples/") and name.endswith(".json"):
                stem, _, epoch = (
                    name.removeprefix("samples/").removesuffix(".json").rpartition("_epoch_")
                )
                if stem and epoch.isdigit():
                    found[(stem, int(epoch))] = info.compress_size
        return found


def slice_eval(
    source: str, destination: Path, samples: Collection[Key], *, all_summaries: bool = False
) -> Copied:
    """Write a log holding `source`'s header and only `samples`, copied as recorded.

    `all_summaries` keeps every summary and reduction (a log that lists the whole run
    and holds the samples copied so far); otherwise only the kept samples' rows stay.
    A sample the source does not hold, or a source with no header or summaries (an
    unfinished run), is an error.
    """
    wanted = {(str(i), int(e)) for i, e in samples}
    copied = Copied()
    with _source(source) as f, ZipFile(f) as z, open(destination, "wb") as out:
        names = z.NameToInfo
        for required in (HEADER, SUMMARIES):
            if required not in names:
                raise ValueError(f"{source} has no {required}: an unfinished log cannot be sliced")
        missing = sorted(k for k in wanted if sample_entry(*k) not in names)
        if missing:
            raise ValueError(f"{source} holds no sample entries for {missing}")
        writer = _Writer(out)
        writer.add_json(HEADER, json.loads(z.read(HEADER)))
        rows = _last_rows(json.loads(z.read(SUMMARIES)))
        writer.add_json(
            SUMMARIES,
            list(rows.values())
            if all_summaries
            else [rows[k] for k in sorted(wanted) if k in rows],
        )
        if REDUCTIONS in names:
            reductions = json.loads(z.read(REDUCTIONS))
            if not all_summaries:
                ids = {i for i, _ in wanted}
                for reduction in reductions:
                    if isinstance(reduction, dict) and isinstance(reduction.get("samples"), list):
                        reduction["samples"] = [
                            s for s in reduction["samples"] if str(s.get("sample_id")) in ids
                        ]
            writer.add_json(REDUCTIONS, reductions)
        for key in sorted(wanted):
            copied.bytes_read += writer.add_raw(
                names[sample_entry(*key)], _raw(f, names[sample_entry(*key)])
            )
            copied.samples.append(key)
        writer.close()
    return copied


def add_samples(source: str, staged: Path, samples: Collection[Key]) -> Copied:
    """Copy more of `source`'s samples into a slice of it, in place.

    Samples the slice already holds are skipped; the rest are appended as recorded and
    the central directory rewritten. A sample the source does not hold is an error.
    """
    with ZipFile(staged) as existing:
        held = {i.filename for i in existing.infolist()}
        kept = list(existing.infolist())
    wanted = sorted(
        {(str(i), int(e)) for i, e in samples if sample_entry(str(i), int(e)) not in held}
    )
    copied = Copied()
    if not wanted:
        return copied
    with _source(source) as f, ZipFile(f) as z:
        names = z.NameToInfo
        missing = [k for k in wanted if sample_entry(*k) not in names]
        if missing:
            raise ValueError(f"{source} holds no sample entries for {missing}")
        with open(staged, "r+b") as out:
            # entries end where the central directory starts: append after the last one
            end = max(
                (i.header_offset + 30 + len(i.filename.encode()) + 20 + i.compress_size)
                for i in kept
            )
            out.seek(end)
            writer = _Writer(out, [(i, i.header_offset) for i in kept])
            for key in wanted:
                info = names[sample_entry(*key)]
                copied.bytes_read += writer.add_raw(info, _raw(f, info))
                copied.samples.append(key)
            writer.close()
    return copied
