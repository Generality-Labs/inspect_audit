"""The planted-defect corpus stays a fair measurement of the auditor.

Fair means the auditor cannot read the answers: what it is given (the benchmark's
source, as the audit copies it, and each sample's metadata) holds no label. Scoring
counts every labelled (item, check), so a run with missing verdicts cannot look better.
"""

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

CORPUS = Path(__file__).parents[1] / "evals" / "defect_bench"
sys.path.insert(0, str(CORPUS / "benchmark"))
sys.path.insert(0, str(CORPUS))

import score_audit  # noqa: E402
import short_answers as defect_bench  # noqa: E402

from inspect_audit._source import collect_source  # noqa: E402
from inspect_audit._task import audit_task  # noqa: E402
from inspect_audit._verdicts import audit_items  # noqa: E402

LABELS = json.loads((CORPUS / "labels.json").read_text())


def test_every_label_is_a_grade_its_check_can_give_for_an_item_that_exists() -> None:
    grades = {item.name: set(item.grades) for item in audit_items()}
    ids = {str(s.id) for s in defect_bench.samples()}
    assert set(LABELS["planted"]) == ids
    for check, grade in LABELS["clean"].items():
        assert grade in grades[check]
    for item, planted in LABELS["planted"].items():
        assert len(planted) <= 1
        for check, grade in planted.items():
            assert grade in grades[check], f"{item}: {check} has no grade {grade!r}"


def test_the_auditor_is_given_no_label() -> None:
    copied = collect_source(Path(defect_bench.__file__).resolve())
    names = {f.name for f in copied.files}
    assert "labels.json" not in names and "score_audit.py" not in names
    text = "\n".join(f.read_text(errors="ignore") for f in copied.files)
    for note in LABELS["notes"].values():
        assert note not in text
    assert "planted" not in text.lower() and "defect" not in text.lower()
    for sample in defect_bench.samples():
        assert not sample.metadata, f"{sample.id} carries metadata the auditor would read"


def test_missing_verdicts_stay_in_the_denominator(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    one = SimpleNamespace(id="q3", scores={"gold-answer": SimpleNamespace(value="INCORRECT")})
    monkeypatch.setattr(score_audit, "read_eval_log", lambda path: SimpleNamespace(samples=[one]))
    log = tmp_path / "audit.eval"
    score_audit.main(str(log))
    rows = (tmp_path / "audit.defects.csv").read_text().splitlines()
    assert len(rows) - 1 == len(LABELS["planted"]) * len(LABELS["clean"])
    out = capsys.readouterr().out
    assert "gold-answer: 1/8 agree; planted found 1/3" in out
    assert "missing 7/8" in out


def test_an_audit_of_the_corpus_builds() -> None:
    task = audit_task(
        defect_bench.short_answers(),
        samples="all",
        checks=sorted(LABELS["clean"]),
        source=defect_bench.__file__,
        auditor_image="auditor:latest",
        cost_limit=0.5,
        judge={},  # an exact-match grader calls no model
    )
    assert len(task.dataset) == len(defect_bench.ITEMS)
