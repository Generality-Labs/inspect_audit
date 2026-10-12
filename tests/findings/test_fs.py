"""StoreFS: one way to read and write a store, on a directory or any fsspec backend."""

from pathlib import Path

import pytest

from inspect_audit.findings.fs import StoreFS, as_store_fs


@pytest.fixture(params=["local", "memory"])
def fs(request: pytest.FixtureRequest, tmp_path: Path) -> StoreFS:
    if request.param == "local":
        return StoreFS.from_locator(tmp_path / "store")
    return StoreFS.from_locator(f"memory://store-{id(request)}")


def test_round_trip_and_listing_are_sorted_and_relative(fs: StoreFS) -> None:
    fs.write_text("b-eval/runs/x-2.run.json", "{}")
    fs.write_text("a-eval/runs/x-1.run.json", "{}")
    fs.write_text("a-eval/SUMMARY.md", "# a")
    assert fs.read_text("a-eval/SUMMARY.md") == "# a"
    assert fs.exists("a-eval/SUMMARY.md") and not fs.exists("nope")
    assert fs.glob("*/runs/*.run.json") == [
        "a-eval/runs/x-1.run.json",
        "b-eval/runs/x-2.run.json",
    ]
    assert fs.ls("a-eval") == ["a-eval/SUMMARY.md", "a-eval/runs"]
    assert fs.ls("missing") == []


def test_write_creates_parents_and_bytes_round_trip(fs: StoreFS) -> None:
    key = fs.write_bytes("deep/er/file.bin", b"\x00\x01")
    assert key == "deep/er/file.bin" and fs.read_bytes(key) == b"\x00\x01"


def test_local_path_only_for_local_stores(tmp_path: Path) -> None:
    local = StoreFS.from_locator(tmp_path)
    assert local.local_path("x/y") == tmp_path / "x" / "y"
    assert StoreFS.from_locator("memory://elsewhere").local_path("x") is None


def test_as_store_fs_accepts_paths_strings_and_instances(tmp_path: Path) -> None:
    a = as_store_fs(tmp_path)
    assert as_store_fs(str(tmp_path)).locator == a.locator
    assert as_store_fs(a) is a


def test_writes_do_not_create_directories_on_object_stores() -> None:
    from fsspec.implementations.memory import MemoryFileSystem

    class Bucketish(MemoryFileSystem):
        protocol = "bucketish"

        def makedirs(self, path: str, exist_ok: bool = False) -> None:
            raise PermissionError("a bucket-scoped token may not create buckets")

    fs = StoreFS(Bucketish(), "bucket/prefix")
    assert fs.write_text("a/b/c.json", "{}") == "a/b/c.json"
    assert fs.read_text("a/b/c.json") == "{}"


def test_put_tree_uploads_every_file_under_a_prefix(tmp_path: Path) -> None:
    fs = StoreFS.from_locator("memory://put-tree")
    (tmp_path / "scan" / "deep").mkdir(parents=True)
    (tmp_path / "scan" / "scan_summary.json").write_text("{}")
    (tmp_path / "scan" / "deep" / "rows.json").write_text("[]")
    keys = fs.put_tree(tmp_path / "scan", "e/scans/run-1")
    assert keys == ["e/scans/run-1/deep/rows.json", "e/scans/run-1/scan_summary.json"]
    assert fs.read_text("e/scans/run-1/scan_summary.json") == "{}"


def test_read_texts_and_write_texts_round_trip_in_one_call_each(fs: StoreFS) -> None:
    keys = fs.write_texts({"a/runs/x.json": "1", "b/deep/y.json": "2", "c.json": "3"})
    assert keys == ["a/runs/x.json", "b/deep/y.json", "c.json"]
    assert fs.read_texts(["c.json", "a/runs/x.json"]) == {"c.json": "3", "a/runs/x.json": "1"}
    assert fs.read_texts([]) == {} and fs.write_texts({}) == []


def test_glob_wildcards_stay_within_one_path_segment(fs: StoreFS) -> None:
    fs.write_texts(
        {
            "a/runs/x.run.json": "1",
            "a/deep/runs/y.run.json": "2",  # one level too deep: not a run of the store
            "b/runs/notes.json": "3",
            "review/dec-1.json": "4",
            "suppressions.yaml": "5",
        }
    )
    assert fs.glob("*/runs/*.run.json") == ["a/runs/x.run.json"]
    assert fs.glob("review/*.json") == ["review/dec-1.json"]
    assert fs.glob("*.yaml") == ["suppressions.yaml"]
    assert fs.glob("missing/*.json") == []


def test_glob_on_an_object_store_lists_once_instead_of_per_prefix() -> None:
    fs = StoreFS.from_locator("memory://glob-once")
    fs.write_texts({f"e{i}/runs/r.run.json": "{}" for i in range(5)})
    calls = {"glob": 0, "find": 0}
    inner_glob, inner_find = fs.fs.glob, fs.fs.find

    def counting_glob(*args: object, **kwargs: object) -> object:
        calls["glob"] += 1
        return inner_glob(*args, **kwargs)

    def counting_find(*args: object, **kwargs: object) -> object:
        calls["find"] += 1
        return inner_find(*args, **kwargs)

    fs.fs.glob, fs.fs.find = counting_glob, counting_find  # type: ignore[method-assign]
    try:
        assert len(fs.glob("*/runs/*.run.json")) == 5
    finally:
        fs.fs.glob, fs.fs.find = inner_glob, inner_find  # type: ignore[method-assign]
    assert calls == {"glob": 0, "find": 1}


def test_put_tree_uploads_in_one_batch(tmp_path: Path) -> None:
    fs = StoreFS.from_locator("memory://put-tree-batch")
    for i in range(5):
        (tmp_path / "scan" / f"{i}.json").parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / "scan" / f"{i}.json").write_text(str(i))
    calls = {"pipe": 0, "open": 0}
    inner_pipe, inner_open = fs.fs.pipe, fs.fs.open

    def counting_pipe(*args: object, **kwargs: object) -> object:
        calls["pipe"] += 1
        return inner_pipe(*args, **kwargs)

    def counting_open(*args: object, **kwargs: object) -> object:
        calls["open"] += 1
        return inner_open(*args, **kwargs)

    fs.fs.pipe, fs.fs.open = counting_pipe, counting_open  # type: ignore[method-assign]
    try:
        keys = fs.put_tree(tmp_path / "scan", "e/scans/r")
    finally:
        fs.fs.pipe, fs.fs.open = inner_pipe, inner_open  # type: ignore[method-assign]
    # one batch call; the memory backend implements it with an open per file, s3fs concurrently
    assert len(keys) == 5 and calls["pipe"] == 1
    assert fs.read_text("e/scans/r/3.json") == "3"
