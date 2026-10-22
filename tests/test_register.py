"""The register: one machine-readable record of every take in an investigation."""

import asyncio
import json
from types import SimpleNamespace
from typing import Any

import pytest
from inspect_ai import Task, task
from inspect_ai.dataset import Sample
from inspect_ai.tool import ToolError
from inspect_ai.util._store import Store, init_subtask_store

from inspect_audit import _investigate, _register
from inspect_audit._verdicts import audit_items


@task
def register_fixture() -> Task:
    # no ids of their own: the register numbers them as an eval would, 1..3
    return Task(dataset=[Sample(input=q, target="x") for q in ("a", "b", "c")])


@pytest.fixture
def box(monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    """A fresh sample store, and the box's findings.json captured."""
    init_subtask_store(Store())
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
    return written


def _audit_log(*scores: tuple[str, int, str, str]) -> Any:
    """An audit log as Inspect reads it: (sample, epoch, check, grade) scores."""
    samples: dict[tuple[str, int], Any] = {}
    for sample_id, epoch, check, grade in scores:
        sample = samples.setdefault(
            (sample_id, epoch), SimpleNamespace(id=sample_id, epoch=epoch, scores={})
        )
        sample.scores[check] = SimpleNamespace(
            value=grade,
            explanation=f"{grade} because",
            metadata={"evidence": [{"observed": "o", "source": "s"}], "flagged": False},
        )
    return SimpleNamespace(
        samples=list(samples.values()),
        eval=SimpleNamespace(eval_id="ev1", model="openrouter/openai/gpt-6-luna"),
    )


def _start(checks: list[str]) -> str:
    return asyncio.run(
        _investigate.start_register("register_fixture")("{}", checks)  # type: ignore[arg-type]
    )


def test_the_population_is_the_benchmarks_items_with_an_empty_cell_per_check(
    box: dict[str, str],
) -> None:
    assert "3 items x 2 checks" in _start(["gold-answer", "answer-format"])
    register = _register.load()
    assert register.subject["ids"] == "positional"
    assert register.samples == {
        "1": {"gold-answer": [], "answer-format": []},
        "2": {"gold-answer": [], "answer-format": []},
        "3": {"gold-answer": [], "answer-format": []},
    }
    # the box holds the same thing, for the agent to read and script against
    from inspect_audit._findings import DRAFT, auditor_results

    assert json.loads(box[DRAFT])["auditors"] == auditor_results(register)
    assert "/workspace/register.json" not in box
    with pytest.raises(ToolError, match="already started"):
        _start(["gold-answer"])


def test_auditor_verdicts_arrive_as_takes_with_their_origin_once(box: dict[str, str]) -> None:
    _start(["gold-answer"])
    register = _register.load()
    skills = {s.name: s for s in audit_items()}
    log = _audit_log(("1", 1, "gold-answer", "INCORRECT"), ("2", 1, "gold-answer", "NO_VERDICT"))
    added = _register.import_audit_log(register, log, "hawk:audit-1", skills)
    assert added == ["1/gold-answer=INCORRECT", "2/gold-answer=NO_VERDICT"]
    [take] = register.samples["1"]["gold-answer"]
    assert (take.by, take.label, take.run, take.eval_id, take.epoch) == (
        "auditor",
        "INCORRECT",
        "hawk:audit-1",
        "ev1",
        1,
    )
    assert take.evidence[0].source == "s" and take.record is not None
    # collecting the same job again adds nothing
    assert _register.import_audit_log(register, log, "hawk:audit-1", skills) == []
    with pytest.raises(ValueError, match="not in the register"):
        _register.import_audit_log(
            register, _audit_log(("99", 1, "gold-answer", "CORRECT")), "hawk:a", skills
        )


def test_the_views_select_cells_by_state(box: dict[str, str]) -> None:
    _start(["gold-answer"])
    register = _register.load()
    skills = {s.name: s for s in audit_items()}
    _register.import_audit_log(
        register,
        _audit_log(("1", 1, "gold-answer", "INCORRECT"), ("2", 1, "gold-answer", "UNVERIFIABLE")),
        "hawk:a",
        skills,
    )
    _register.import_audit_log(
        register, _audit_log(("1", 1, "gold-answer", "CORRECT")), "hawk:b", skills
    )

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
        _register.Take(by="investigator", label="INCORRECT", explanation="e")
    )
    assert cells("flagged") == []
    assert cells("disputed") == ["1"]


def test_collecting_an_audit_job_fills_the_register_from_its_real_log(
    box: dict[str, str], tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The verdicts come from the .eval file through Inspect's log API, as on Hawk."""
    from pathlib import Path

    from inspect_ai import eval as inspect_eval
    from inspect_ai.scorer import Score, scorer

    from inspect_audit import _investigation_workspace

    @scorer(metrics=[], name="gold-answer")
    def verdict():
        async def score(state: Any, target: Any) -> Score:
            return Score(
                value="INCORRECT" if state.sample_id == 2 else "CORRECT",
                explanation="checked against the source",
                metadata={"evidence": [{"observed": "the source says 9", "source": "url"}]},
            )

        return score

    audit = Task(
        dataset=[Sample(input=q, target="x", id=i) for i, q in ((1, "a"), (2, "b"))],
        scorer=verdict(),
    )
    [log] = inspect_eval(audit, model="mockllm/model", log_dir=str(tmp_path), display="none")
    _start(["gold-answer"])

    class Runs:
        async def status(self, run_id: str) -> str:
            return "complete"

        async def logs(self, run_id: str, out_dir: Path) -> list[Path]:
            return [Path(log.location)]

    async def nowhere(files: list[Path], destination: str) -> None:
        pass

    monkeypatch.setattr(_investigation_workspace, "copy_into_box", nowhere)
    inv = _investigate.Investigation(
        root=tmp_path,
        runs=Runs(),  # type: ignore[arg-type]
        where="hawk",
        task="register_fixture",
        task_package="p",
        audit_package="a",
        auditor_image="i",
        menu=_investigate.ModelMenu(auditors=[], subjects=[], judges=[]),
        supplied=[],
    )
    record = _investigate.store_as(_investigate.InvestigationRecord)
    record.jobs = [{"label": "a1", "kind": "audit", "run_id": "r", "address": "hawk:audit-1"}]
    out = asyncio.run(_investigate.jobs(inv)("collect", "a1", None))  # type: ignore[arg-type]
    assert "2 auditor take(s) added, 1 flagged" in out
    register = _register.load()
    [take] = register.samples["2"]["gold-answer"]
    assert (take.label, take.run, take.evidence[0].observed) == (
        "INCORRECT",
        "hawk:audit-1",
        "the source says 9",
    )
    assert register.samples["3"]["gold-answer"] == []  # not audited: still missing
