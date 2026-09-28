"""Finding eval sets on Hawk by task, and pulling their logs into a local cache."""

import sys
from pathlib import Path

import pytest
from test_header_adapter import _log

from inspect_audit.findings.adapters import ProducerError
from inspect_audit.findings.hawk import (
    EvalSetInfo,
    download_eval_set,
    find_eval_sets,
    matches_task,
)
from inspect_audit.findings.producers import ProducerConfig

STUB = Path(__file__).parent / "stubs" / "hawk_stub.py"

PAGES = [
    {"items": [
        {"eval_set_id": "scicode-a", "created_at": "2026-09-24T08:46:40Z", "created_by": "u1",
         "eval_count": 9, "task_names": ["inspect_evals/scicode"]},
        {"eval_set_id": "chess-1", "created_at": "2026-09-25T15:40:58Z", "created_by": "u2",
         "eval_count": 1, "task_names": ["inspect_audit/investigate"]},
    ], "total": 3, "page": 1, "limit": 2},
    {"items": [
        {"eval_set_id": "imported-scicode", "created_at": "2026-09-04T02:38:06Z", "created_by": "u3",
         "eval_count": 3, "task_names": ["scicode", "exploitbench/exploit_bench"]},
    ], "total": 3, "page": 2, "limit": 2},
]


async def _fake_pages(page: int, limit: int) -> dict[str, object]:
    return PAGES[page - 1]


def test_matches_task_on_the_unqualified_name() -> None:
    assert matches_task(["inspect_evals/scicode"], "inspect_evals/scicode")
    assert matches_task(["scicode"], "inspect_evals/scicode")
    assert matches_task(["inspect_evals/scicode"], "scicode")
    assert not matches_task(["inspect_evals/scicode_replay"], "inspect_evals/scicode")
    assert not matches_task([], "inspect_evals/scicode")
    # the sample auditor's task is audit/<pkg>/<task>: same tail, different task. Only a bare
    # name (no slash) may match on the tail; two qualified names must match exactly.
    assert not matches_task(["audit/inspect_evals/scicode"], "inspect_evals/scicode")
    assert not matches_task(["inspect_evals/scicode"], "audit/inspect_evals/scicode")


def test_find_eval_sets_pages_and_filters() -> None:
    found = find_eval_sets("inspect_evals/scicode", fetch_page=_fake_pages, page_size=2)
    assert [s.eval_set_id for s in found] == ["scicode-a", "imported-scicode"]  # newest first
    assert isinstance(found[0], EvalSetInfo)
    assert found[0].eval_count == 9 and found[0].task_names == ["inspect_evals/scicode"]


def test_find_eval_sets_with_no_match_is_empty() -> None:
    assert find_eval_sets("inspect_evals/hle", fetch_page=_fake_pages, page_size=2) == []


def test_download_eval_set_uses_the_cli_and_returns_cached_files(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    src = _log(tmp_path / "src")
    monkeypatch.setenv("STUB_EVAL_SRC", str(src))
    monkeypatch.setenv("STUB_HAWK_LOG", str(tmp_path / "calls.log"))
    config = ProducerConfig(hawk=(sys.executable, str(STUB)))
    cache = tmp_path / "cache"
    files = download_eval_set("scicode-a", cache, config)
    assert [f.name for f in files] == [src.name]
    assert files[0].parent == cache / "scicode-a"
    calls = (tmp_path / "calls.log").read_text().splitlines()
    assert calls == [f"download scicode-a -o {cache / 'scicode-a'}"]
    # a second call still asks the CLI (it skips present files itself) and returns the same files
    assert download_eval_set("scicode-a", cache, config) == files


def test_download_eval_set_failure_raises_producer_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    src = _log(tmp_path / "src")
    monkeypatch.setenv("STUB_EVAL_SRC", str(src))
    monkeypatch.setenv("STUB_EXIT", "1")
    config = ProducerConfig(hawk=(sys.executable, str(STUB)))
    with pytest.raises(ProducerError, match="hawk download"):
        download_eval_set("scicode-a", tmp_path / "cache", config)


def test_hawk_command_override_is_shell_split() -> None:
    config = ProducerConfig.from_env({"INSPECT_AUDIT_HAWK_CMD": "uv run hawk"})
    assert config.hawk == ("uv", "run", "hawk")
    assert ProducerConfig().hawk == ("hawk",)


def test_manifest_loads_logs_and_artifacts(tmp_path: Path) -> None:
    from inspect_audit.findings.hawk import load_manifest

    manifest = tmp_path / "m.yaml"
    manifest.write_text(
        "dest: artefacts/hawk\nlogs:\n  - id: set-a\n    note: a\n  - id: set-b\nartifacts:\n  - id: inv-1\n    note: bundle\n"
    )
    loaded = load_manifest(manifest)
    assert loaded.dest == Path("artefacts/hawk")
    assert [(e.id, e.note) for e in loaded.logs] == [("set-a", "a"), ("set-b", "")]
    assert [(e.id, e.note) for e in loaded.artifacts] == [("inv-1", "bundle")]


def test_manifest_rejects_unknown_keys_and_missing_ids(tmp_path: Path) -> None:
    from inspect_audit.findings.hawk import load_manifest

    bad = tmp_path / "m.yaml"
    bad.write_text("dest: x\nlogs:\n  - note: no id\n")
    with pytest.raises(ValueError):
        load_manifest(bad)
    bad.write_text("dest: x\nbogus: 1\n")
    with pytest.raises(ValueError):
        load_manifest(bad)


def test_pull_manifest_fetches_logs_and_artifacts_into_dest(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from inspect_audit.findings.hawk import Manifest, ManifestEntry, pull_manifest

    src = _log(tmp_path / "src")
    monkeypatch.setenv("STUB_EVAL_SRC", str(src))
    monkeypatch.setenv("STUB_HAWK_LOG", str(tmp_path / "calls.log"))
    config = ProducerConfig(hawk=(sys.executable, str(STUB)))
    manifest = Manifest(dest=tmp_path / "dest", logs=[ManifestEntry(id="set-a", note="")], artifacts=[ManifestEntry(id="inv-1", note="")])
    report = pull_manifest(manifest, config)
    assert (tmp_path / "dest" / "logs" / "set-a" / src.name).is_file()
    assert (tmp_path / "dest" / "artifacts" / "inv-1" / "bundle.txt").is_file()
    calls = (tmp_path / "calls.log").read_text().splitlines()
    assert calls[0].startswith("download set-a -o ")
    assert calls[1].startswith("download-artifacts inv-1 -o ")
    assert [(r.kind, r.id, r.files) for r in report] == [("logs", "set-a", 1), ("artifacts", "inv-1", 1)]


def test_pull_manifest_records_failures_and_continues(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from inspect_audit.findings.hawk import Manifest, ManifestEntry, pull_manifest

    src = _log(tmp_path / "src")
    monkeypatch.setenv("STUB_EVAL_SRC", str(src))
    monkeypatch.setenv("STUB_FAIL_ID", "set-bad")
    config = ProducerConfig(hawk=(sys.executable, str(STUB)))
    manifest = Manifest(dest=tmp_path / "dest", logs=[ManifestEntry(id="set-bad", note=""), ManifestEntry(id="set-ok", note="")], artifacts=[])
    report = pull_manifest(manifest, config)
    assert [(r.id, r.files, r.error is None) for r in report] == [("set-bad", 0, False), ("set-ok", 1, True)]
