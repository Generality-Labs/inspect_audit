"""One end-to-end run under `mockllm`.

The unit tests only assemble tasks, which cannot catch anything that fails while an
eval actually executes — an unregistered solver, a sandbox that will not build, a file
that will not copy. This runs the real thing, with no model spend.

Requires Docker; deselect with `-m "not docker"`.
"""

from pathlib import Path

import pytest
from inspect_ai import Task, eval
from inspect_ai.dataset import MemoryDataset, Sample
from inspect_ai.scorer import match

from inspect_audit import audit_task
from inspect_audit._case import AUDIT_ROOT

pytestmark = pytest.mark.docker


def test_an_audit_runs_and_its_sandbox_holds_the_case(tmp_path: Path) -> None:
    task = Task(
        name="fixture_task",
        dataset=MemoryDataset([Sample(id=1, input="a question", target="an answer")]),
        scorer=match(),
    )

    logs = eval(
        audit_task(task, trajectories=False),
        model="mockllm/model",
        log_dir=str(tmp_path),
        display="none",
    )

    log = logs[0]
    assert log.status == "success", log.error
    assert log.samples is not None

    files = log.samples[0].store.get("audit_files", [])
    assert f"{AUDIT_ROOT}/sample.json" in files
    assert f"{AUDIT_ROOT}/gold/target.txt" in files
    assert f"{AUDIT_ROOT}/gold/grading.md" in files
    assert log.samples[0].store.get("case_readback")
