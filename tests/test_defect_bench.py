"""The planted-defect corpus stays usable as a measurement of the auditor."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "evals" / "defect_bench"))

import defect_bench

from inspect_audit._task import audit_task
from inspect_audit._verdicts import audit_items


def test_every_label_is_a_grade_its_check_can_give() -> None:
    grades = {item.name: set(item.grades) for item in audit_items()}
    for sample in defect_bench.samples():
        for check, expected in (sample.metadata or {})["expected"].items():
            assert expected in grades[check], f"{sample.id}: {check} has no grade {expected!r}"


def test_each_defect_is_planted_once_and_some_items_are_clean() -> None:
    planted = [len((s.metadata or {})["planted"]) for s in defect_bench.samples()]
    assert set(planted) <= {0, 1}
    assert planted.count(0) >= 2


def test_an_audit_of_the_corpus_builds(tmp_path: Path) -> None:
    task = audit_task(
        defect_bench.defect_bench(),
        samples="all",
        checks=sorted(defect_bench.CLEAN),
        source=defect_bench.__file__,
        auditor_image="auditor:latest",
        cost_limit=0.5,
        judge={},  # an exact-match grader calls no model
    )
    assert len(task.dataset) == len(defect_bench.ITEMS)
