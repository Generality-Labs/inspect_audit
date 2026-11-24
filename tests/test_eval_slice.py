"""Slices of .eval logs: entries copied as recorded, from local and remote logs alike."""

import json
import zipfile
from pathlib import Path

import fsspec
import pytest
from inspect_ai.log import (
    read_eval_log,
    read_eval_log_sample,
    read_eval_log_sample_summaries,
)

from inspect_audit._eval_slice import add_samples, recorded_samples, slice_eval


@pytest.fixture(scope="module")
def recorded(tmp_path_factory: pytest.TempPathFactory) -> str:
    """A log written by Inspect's own writer: three samples, two epochs each, scored."""
    from test_helpers.logs import write_fixture_log

    return write_fixture_log(str(tmp_path_factory.mktemp("recorded") / "run.eval"))


def _raw(path: str | Path, name: str) -> bytes:
    """An entry's stored (compressed) bytes."""
    with zipfile.ZipFile(path) as z:
        info = z.getinfo(name)
        with open(path, "rb") as f:
            f.seek(info.header_offset + 26)
            n, e = int.from_bytes(f.read(2), "little"), int.from_bytes(f.read(2), "little")
            f.seek(info.header_offset + 30 + n + e)
            return f.read(info.compress_size)


def test_a_slice_holds_only_its_samples_copied_byte_for_byte(recorded: str, tmp_path: Path):
    out = tmp_path / "slice.eval"
    copied = slice_eval(recorded, out, [("b", 1), ("b", 2)])
    assert copied.samples == [("b", 1), ("b", 2)]
    with zipfile.ZipFile(out) as z:
        assert sorted(n for n in z.namelist() if n.startswith("samples/")) == [
            "samples/b_epoch_1.json",
            "samples/b_epoch_2.json",
        ]
    for name in ("samples/b_epoch_1.json", "samples/b_epoch_2.json"):
        assert _raw(out, name) == _raw(recorded, name)  # never recompressed
    assert copied.bytes_read == sum(
        zipfile.ZipFile(recorded).getinfo(f"samples/b_epoch_{e}.json").compress_size
        for e in (1, 2)
    )
    header = read_eval_log(str(out), header_only=True)
    assert header.eval.eval_id == read_eval_log(recorded, header_only=True).eval.eval_id
    assert [(s.id, s.epoch) for s in read_eval_log_sample_summaries(str(out))] == [
        ("b", 1),
        ("b", 2),
    ]
    for epoch in (1, 2):
        sample = read_eval_log_sample(str(out), id="b", epoch=epoch, resolve_attachments=True)
        assert sample == read_eval_log_sample(recorded, id="b", epoch=epoch, resolve_attachments=True)
        assert sample.messages and sample.scores
    full = read_eval_log(str(out))
    assert [(s.id, s.epoch) for s in full.samples or []] == [("b", 1), ("b", 2)]
    with pytest.raises(IndexError):
        read_eval_log_sample(str(out), id="a", epoch=1)


def test_a_sample_the_log_does_not_hold_is_refused(recorded: str, tmp_path: Path):
    with pytest.raises(ValueError, match="no sample entries"):
        slice_eval(recorded, tmp_path / "x.eval", [("z", 1)])


def test_a_remote_log_is_sliced_through_fsspec(recorded: str, tmp_path: Path):
    """Any fsspec URL Inspect reads (Hawk's hawksrc://, s3://) is sliced the same way."""
    remote = "memory://logs/run.eval"
    with fsspec.open(remote, "wb") as f:
        f.write(Path(recorded).read_bytes())
    out = tmp_path / "slice.eval"
    slice_eval(remote, out, [("c", 2)])
    assert read_eval_log_sample(str(out), id="c", epoch=2) == read_eval_log_sample(
        recorded, id="c", epoch=2
    )
    assert recorded_samples(remote).keys() == {(i, e) for i in "abc" for e in (1, 2)}


def test_samples_are_added_to_a_log_that_lists_the_whole_run(recorded: str, tmp_path: Path):
    """The auditor's ranged staging: every summary up front, samples copied on demand."""
    stub = tmp_path / "run.eval"
    slice_eval(recorded, stub, [], all_summaries=True)
    assert len(read_eval_log_sample_summaries(str(stub))) == 6
    with zipfile.ZipFile(stub) as z:
        assert not [n for n in z.namelist() if n.startswith("samples/")]
    assert add_samples(recorded, stub, [("a", 1)]).samples == [("a", 1)]
    assert add_samples(recorded, stub, [("a", 1), ("c", 2)]).samples == [("c", 2)]
    assert add_samples(recorded, stub, [("a", 1)]).samples == []
    with zipfile.ZipFile(stub) as z:
        assert z.testzip() is None
        assert sorted(n for n in z.namelist() if n.startswith("samples/")) == [
            "samples/a_epoch_1.json",
            "samples/c_epoch_2.json",
        ]
    for sample_id, epoch in (("a", 1), ("c", 2)):
        assert read_eval_log_sample(str(stub), id=sample_id, epoch=epoch) == read_eval_log_sample(
            recorded, id=sample_id, epoch=epoch
        )
    assert len(read_eval_log_sample_summaries(str(stub))) == 6


def test_a_sample_written_twice_resolves_to_its_last_entry_as_inspect_reads_it(
    recorded: str, tmp_path: Path
):
    """A retried run can hold every sample twice; Inspect's readers use the later one."""
    retried = tmp_path / "retried.eval"
    retried.write_bytes(Path(recorded).read_bytes())
    with zipfile.ZipFile(recorded) as z:
        rerun = json.loads(z.read("samples/a_epoch_1.json"))
        rows = json.loads(z.read("summaries.json"))
    rerun["metadata"] = {"rerun": True}
    row = next(r for r in rows if r["id"] == "a" and r["epoch"] == 1)
    import warnings

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")  # zipfile's duplicate-name warning
        with zipfile.ZipFile(retried, "a", compression=zipfile.ZIP_DEFLATED) as z:
            z.writestr("samples/a_epoch_1.json", json.dumps(rerun))
            z.writestr("summaries.json", json.dumps(rows + [dict(row, metadata={"rerun": True})]))
    assert read_eval_log_sample(str(retried), id="a", epoch=1).metadata == {"rerun": True}
    out = tmp_path / "slice.eval"
    slice_eval(str(retried), out, [("a", 1)])
    with zipfile.ZipFile(out) as z:
        assert z.namelist().count("samples/a_epoch_1.json") == 1
    assert read_eval_log_sample(str(out), id="a", epoch=1).metadata == {"rerun": True}
    [summary] = read_eval_log_sample_summaries(str(out))
    assert summary.metadata == {"rerun": True}
