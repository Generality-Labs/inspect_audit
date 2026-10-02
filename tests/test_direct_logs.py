import asyncio
import csv
import json
from pathlib import Path

import fsspec
import pytest
from test_helpers.logs import run_fixture_eval

from inspect_audit import _investigate as investigation


def test_direct_logs_keep_repeated_ids_distinct_and_read_transcripts(tmp_path, monkeypatch):
    original = Path(run_fixture_eval(str(tmp_path / "original"), epochs=2))
    fs = fsspec.filesystem("memory")
    base = "memory://direct-reader-test/"
    fs.pipe(base + "a/run.eval", original.read_bytes())
    fs.pipe(base + "b/run.eval", original.read_bytes())
    direct = investigation._direct_logs
    files = direct(base, tmp_path / "cache")
    assert len(files) == 2 and files[0].name != files[1].name
    rows = investigation._direct_samples(files, None)
    assert len(rows) == 12
    assert len({row["uuid"] for row in rows}) == 12
    assert len(investigation._direct_samples(files, 1)) == 1

    source = "s3://operator-supplied/inputs/"
    monkeypatch.setattr(
        investigation,
        "_direct_logs",
        lambda address, destination, limit=None: direct(base, destination, limit),
    )
    root = tmp_path / "workspace"
    (root / "inputs").mkdir(parents=True)
    (root / "inputs/seed.json").write_text("{}")
    asyncio.run(investigation.check_evidence_access(None, root, [source])(None, None))
    checks = json.loads((root / "inputs/seed.json").read_text())["evidence_access"]
    assert checks[0]["status"] == "readable"
    assert len(list((root / "inputs/index").rglob("*.eval"))) == 1

    tool = investigation.supplied_logs(None, root, [source])
    alias = investigation.source_alias(source)
    asyncio.run(tool("samples", alias, None, None))
    table = root / "inputs/index" / alias / "samples.csv"
    indexed = list(csv.DictReader(table.open()))
    assert len(indexed) == 12
    result = asyncio.run(tool("transcript", alias, indexed[-1]["uuid"], None))
    assert "wrote" in result
    transcripts = list((root / "inputs/index" / alias / "transcripts").glob("*.md"))
    assert len(transcripts) == 1 and "q3" in transcripts[0].read_text()
    assert "downloaded 2 log(s)" in asyncio.run(tool("fetch", alias, None, None))


def test_failed_direct_download_is_not_cached_as_complete(tmp_path, monkeypatch):
    from contextlib import contextmanager

    class Broken:
        def read(self, size):
            raise OSError("interrupted object download")

    @contextmanager
    def interrupted(*args):
        yield Broken()

    monkeypatch.setattr(fsspec, "open", interrupted)
    with pytest.raises(OSError):
        investigation._direct_logs("s3://operator/trace.eval", tmp_path)
    assert not list(tmp_path.rglob("*.eval"))
    assert not list(tmp_path.rglob("*.partial"))
