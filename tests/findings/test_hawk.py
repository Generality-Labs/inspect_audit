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
