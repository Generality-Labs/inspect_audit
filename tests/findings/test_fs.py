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
