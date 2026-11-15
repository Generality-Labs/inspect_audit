"""The register: one machine-readable record of every take in an audit."""

import asyncio
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from inspect_ai import Task
from inspect_ai.dataset import Sample
from inspect_ai.tool import ToolError
from inspect_ai.util._store import Store, init_subtask_store
from test_helpers.audit import make_audit
from test_helpers.box import local_box

from inspect_audit import _audit, _register
from inspect_audit._verdicts import investigation_items
from inspect_audit.dispatch.policy import Models

FIXTURE = """
from inspect_ai import Task, task
from inspect_ai.dataset import Sample


@task
def register_fixture() -> Task:
    # no ids of their own: the register numbers them as an eval would, 1..3
    return Task(dataset=[Sample(input=q, target="x") for q in ("a", "b", "c")])
"""


@pytest.fixture
def box(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[dict[str, str]]:
    """A fresh sample store, a real box that reads the fixture benchmark, and the box's
    findings.json captured."""
    init_subtask_store(Store())
    site = tmp_path / "site"
    site.mkdir()
    (site / "regfix.py").write_text(FIXTURE)
    monkeypatch.setenv("PYTHONPATH", str(site))
    written: dict[str, str] = {}

    class Box:
        async def write_file(self, path: str, contents: str | bytes) -> None:
            written[path] = contents.decode() if isinstance(contents, bytes) else contents

        async def read_file(self, path: str) -> str:
            if path not in written:
                raise FileNotFoundError(path)
            return written[path]

    from inspect_audit import _findings

    monkeypatch.setattr(_findings, "sandbox", Box)
    with local_box():
        yield written


def _verdicts(run: str, *scores: tuple[str, int, str, str]) -> list[dict[str, Any]]:
    """Investigator verdicts as `_results.investigator_takes` returns them from the
    Scout results: (sample, epoch, check, grade)."""
    return [
        {
            "sample": sample_id,
            "check": check,
            "label": grade,
            "explanation": f"{grade} because",
            "metadata": {"evidence": [{"observed": "o", "source": "s"}], "flagged": False},
            "run": run,
            "eval_id": "ev1",
            "epoch": epoch,
            "model": "openrouter/openai/gpt-6-luna",
            "result": f"r-{sample_id}-{epoch}",
        }
        for sample_id, epoch, check, grade in scores
    ]


def _start(checks: list[str]) -> str:
    """Start the register as the register tool does on first use: from a launch policy."""
    from inspect_ai.util import store_as

    from inspect_audit.dispatch.policy import LaunchPolicy, Models, PolicyStore, ensure_policy

    policy = LaunchPolicy(
        task="register_fixture",
        source="regfix",
        task_package=None,
        investigation_package=None,
        investigator_image="i",
        models=Models(),
        checks=tuple(checks),
        configuration_json="{}",
    )
    saved = store_as(PolicyStore)
    saved.policy_json = saved.digest = None
    ensure_policy(policy, saved, require_existing=False)
    count = asyncio.run(_register.start_register("register_fixture", policy))
    return f"{count} items x {len(checks)} checks"


def test_the_population_is_the_benchmarks_items_with_an_empty_cell_per_check(
    box: dict[str, str],
) -> None:
    assert "3 items x 2 checks" in _start(["gold-answer", "answer-format"])
    register = asyncio.run(_register.load())
    assert register.subject["ids"] == "positional"
    assert register.samples == {
        "1": {"gold-answer": [], "answer-format": []},
        "2": {"gold-answer": [], "answer-format": []},
        "3": {"gold-answer": [], "answer-format": []},
    }
    # nothing is copied into the agent's draft: the verdicts have one place (FRICTION 113)
    assert box == {}
    with pytest.raises(ToolError, match="already started"):
        _start(["gold-answer"])


def test_investigator_verdicts_arrive_as_takes_with_their_origin_once(box: dict[str, str]) -> None:
    _start(["gold-answer"])
    register = asyncio.run(_register.load())
    rows = _verdicts(
        "hawk:investigation-1",
        ("1", 1, "gold-answer", "INCORRECT"),
        ("2", 1, "gold-answer", "NO_VERDICT"),
    )
    added = _register.add_takes(register, rows)
    assert added == ["1/gold-answer=INCORRECT", "2/gold-answer=NO_VERDICT"]
    [take] = register.samples["1"]["gold-answer"]
    assert (take.by, take.label, take.run, take.eval_id, take.epoch) == (
        "investigator",
        "INCORRECT",
        "hawk:investigation-1",
        "ev1",
        1,
    )
    assert take.evidence[0].source == "s" and take.record is not None
    # collecting the same job again adds nothing
    assert _register.add_takes(register, rows) == []
    with pytest.raises(ValueError, match="not in the register"):
        _register.add_takes(register, _verdicts("hawk:a", ("99", 1, "gold-answer", "CORRECT")))


def test_the_views_select_cells_by_state(box: dict[str, str]) -> None:
    _start(["gold-answer"])
    register = asyncio.run(_register.load())
    skills = {s.name: s for s in investigation_items()}
    _register.add_takes(
        register,
        _verdicts(
            "hawk:a", ("1", 1, "gold-answer", "INCORRECT"), ("2", 1, "gold-answer", "UNVERIFIABLE")
        ),
    )
    _register.add_takes(register, _verdicts("hawk:b", ("1", 1, "gold-answer", "CORRECT")))

    def cells(view: str) -> list[str]:
        rows = _register.select(register, view, skills, None)  # type: ignore[arg-type]
        return [r["sample"] for r in rows]  # type: ignore[index]

    assert cells("flagged") == ["1"]
    assert cells("disputed") == ["1"]
    assert cells("unresolved") == ["2"]
    assert cells("missing") == ["3"]
    summary = _register.select(register, "summary", skills, None)
    assert summary == {
        "gold-answer": {"cells": 3, "missing": 1, "flagged": 1, "unresolved": 1, "disputed": 1}
    }
    # a review settles the flag; the dispute stays on the record
    register.samples["1"]["gold-answer"].append(
        _register.Take(by="auditor", label="INCORRECT", explanation="e")
    )
    assert cells("flagged") == []
    assert cells("disputed") == ["1"]


LONG_EXPLANATION = "Checked against the cited source, paragraph by paragraph. " * 40


def test_collecting_an_investigation_job_fills_the_register_from_its_real_log(
    box: dict[str, str], tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The verdicts come from the real .eval file into the checkpointed store, so they
    survive a resume on a new machine; the job keeps each log's identity (FRICTION 113)."""
    from pathlib import Path

    from inspect_ai import eval as inspect_eval
    from inspect_ai.scorer import Score, scorer

    from inspect_audit import _box

    @scorer(metrics=[], name="gold-answer")
    def verdict():
        async def score(state: Any, target: Any) -> Score:
            # as large as a real investigator's: summaries would cut both (FRICTION 125)
            return Score(
                value="INCORRECT" if state.sample_id == 2 else "CORRECT",
                explanation=LONG_EXPLANATION,
                metadata={
                    "evidence": [{"observed": "the source says 9", "source": "url"}]
                    + [{"observed": f"note {n} " + "x" * 80, "source": "url"} for n in range(20)]
                },
            )

        return score

    investigation = Task(
        dataset=[Sample(input=q, target="x", id=i) for i, q in ((1, "a"), (2, "b"))],
        scorer=verdict(),
    )
    [log] = inspect_eval(
        investigation, model="mockllm/model", log_dir=str(tmp_path), display="none"
    )
    _start(["gold-answer"])

    class Runs:
        async def status(self, run_id: str) -> str:
            return "complete"

        async def logs(self, run_id: str, out_dir: Path) -> list[Path]:
            return [Path(log.location)]

    async def nowhere(files: list[Path], destination: str) -> None:
        pass

    monkeypatch.setattr(_box, "copy_into_box", nowhere)
    inv = make_audit(
        root=tmp_path,
        runs=Runs(),  # type: ignore[arg-type]
        task="register_fixture",
        task_package="p",
        investigation_package="a",
        investigator_image="i",
        menu=Models(investigators=[], subjects=[], judges=[]),
        supplied=[],
    )
    record = _audit.store_as(_audit.AuditRecord)
    record.jobs = [
        {"label": "a1", "kind": "investigation", "run_id": "r", "address": "hawk:investigation-1"}
    ]
    out = asyncio.run(_audit.jobs(inv)("collect", "a1", None))  # type: ignore[arg-type]
    assert "2 investigator verdict(s) added, 1 flagged" in out
    again = asyncio.run(_audit.jobs(inv)("collect", "a1", None))  # type: ignore[arg-type]
    assert "0 investigator verdict(s) added, 0 flagged" in again
    # a resume runs on another machine: the host's copies are gone, the store is not
    import shutil

    shutil.rmtree(tmp_path / "inputs", ignore_errors=True)
    Path(log.location).rename(Path(log.location).with_suffix(".moved"))
    register = asyncio.run(_register.load())
    [cited] = record.jobs[0]["logs"]
    assert cited["address"].startswith("hawk:investigation-1/") and len(cited["sha256"]) == 64
    from inspect_audit._register import RegisterStore

    assert len(_audit.store_as(RegisterStore).verdicts) == 2
    [take] = register.samples["2"]["gold-answer"]
    assert (take.label, take.run, take.evidence[0].observed) == (
        "INCORRECT",
        "hawk:investigation-1",
        "the source says 9",
    )
    # whole, not a summary's preview
    assert len(take.evidence) == 21 and take.explanation == LONG_EXPLANATION
    assert register.samples["3"]["gold-answer"] == []  # not investigated: still missing


def test_failed_verdict_import_does_not_mark_job_collected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, box: dict[str, str], hawk: Any
) -> None:
    """A job whose verdicts cannot enter the register stays uncollected, so collecting
    it again after the cause is fixed still brings its verdicts in."""
    from inspect_ai import eval as inspect_eval
    from inspect_ai.scorer import Score, scorer
    from inspect_ai.tool import ToolError
    from test_helpers.fake_hawk import FakeJob

    from inspect_audit import _box
    from inspect_audit.dispatch import HawkRuns

    @scorer(metrics=[], name="gold-answer")
    def verdict():
        async def score(state: Any, target: Any) -> Score:
            return Score(value="CORRECT", explanation="checked", metadata={"evidence": []})

        return score

    def investigation_log(ids: list[int], where: str) -> Path:
        [log] = inspect_eval(
            Task(dataset=[Sample(input="q", target="x", id=i) for i in ids], scorer=verdict()),
            model="mockllm/model",
            log_dir=str(tmp_path / where),
            display="none",
        )
        return Path(log.location)

    _start(["gold-answer"])  # the population: items 1, 2, 3

    async def nowhere(files: list[Path], destination: str) -> None:
        pass  # the job's logs placed in the box's /inputs: no such folder on this machine

    monkeypatch.setattr(_box, "copy_into_box", nowhere)
    inv = make_audit(
        root=tmp_path,
        runs=HawkRuns(hawk.url, credential_mode="restricted"),
        task="register_fixture",
        task_package="p",
        investigation_package="a",
        investigator_image="i",
        menu=Models(investigators=[], subjects=[], judges=[]),
        supplied=[],
    )
    # a verdict on an item the register does not hold: the whole collection is refused
    hawk.jobs["bad"] = FakeJob(
        config={}, body={}, status="complete", logs=[investigation_log([9], "bad")]
    )
    hawk.jobs["good"] = FakeJob(
        config={}, body={}, status="complete", logs=[investigation_log([1, 2], "good")]
    )
    record = inv.record()
    record.jobs = [
        {"label": label, "kind": "investigation", "run_id": label, "address": f"hawk:{label}"}
        for label in ("bad", "good")
    ]
    collect = _audit.jobs(inv)
    with pytest.raises(ToolError, match="not in the register's population"):
        asyncio.run(collect("collect", "bad", None))
    assert not record.jobs[0].get("collected")
    assert asyncio.run(_register.load()).samples["1"]["gold-answer"] == []
    # a transient failure (Hawk unreachable) leaves the job uncollected, so a retry works
    hawk.down = True
    with pytest.raises(ToolError, match="failed"):
        asyncio.run(collect("collect", "good", None))
    assert not record.jobs[1].get("collected")
    hawk.down = False
    out = asyncio.run(collect("collect", "good", None))
    assert "2 investigator verdict(s) added" in out and record.jobs[1]["collected"]


def test_an_unreadable_population_says_why_and_does_not_block_submission(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, box: dict[str, str]
) -> None:
    """Breadth round, docvqa: reading the items failed in the box, the error shown was a
    progress bar, and submission then refused for ever ("Open the register first")."""
    from inspect_ai.tool import ToolError

    from inspect_audit._in_box import why_it_failed
    from inspect_audit._register import RegisterStore

    noisy = (
        "Saving: 10%\rSaving: 49%\n" * 200
        + "Traceback (most recent call last):\n  x\nMemoryError\n"
    )
    assert why_it_failed(noisy).startswith("Traceback") and "MemoryError" in why_it_failed(noisy)

    # a benchmark whose dataset cannot be built in the box
    site = tmp_path / "broken"
    site.mkdir()
    (site / "brokenfix.py").write_text(
        "from inspect_ai import Task, task\n\n@task\ndef register_fixture() -> Task:\n"
        "    raise MemoryError('dataset too large for the box')\n"
    )
    monkeypatch.setenv("PYTHONPATH", str(site))
    from inspect_audit.dispatch.policy import LaunchPolicy, Models, PolicyStore, ensure_policy

    policy = LaunchPolicy(
        task="register_fixture",
        source="brokenfix",
        task_package=None,
        investigation_package=None,
        investigator_image="i",
        models=Models(),
        checks=("gold-answer",),
        configuration_json="{}",
    )
    saved = _register.store_as(PolicyStore)
    saved.policy_json = saved.digest = None
    ensure_policy(policy, saved, require_existing=False)
    with pytest.raises(ToolError, match=r"(?s)exit 1\):\nTraceback.*MemoryError"):
        asyncio.run(_register.start_register("register_fixture", policy))
    stored = _register.store_as(RegisterStore)
    assert "MemoryError" in stored.unavailable
    assert "MemoryError" in str(stored.subject["register_unavailable"])
    # and submission goes on without a register instead of refusing for ever
    from inspect_audit._findings import submit_findings

    refusal = ""
    try:
        asyncio.run(submit_findings(tmp_path, None, "register_fixture")())
    except ToolError as ex:
        refusal = str(ex)  # it may fail later, on the empty draft: not at the register
    assert "Open the register" not in refusal, "the dead end is back"
