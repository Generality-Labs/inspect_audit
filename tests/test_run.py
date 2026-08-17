"""One end-to-end run under `mockllm`.

The unit tests only assemble tasks, which cannot catch anything that fails while an
eval actually executes — an unregistered solver, a sandbox that will not build, a file
that will not copy, a log that arrives but will not open. This runs the real thing,
with no model spend.

Requires Docker; deselect with `-m "not docker"`.
"""

from pathlib import Path

import pytest
from inspect_ai import eval
from test_helpers.logs import fixture_task, run_fixture_eval

from inspect_audit import audit_task

pytestmark = pytest.mark.docker


def test_a_container_is_spun_out_and_its_logs_open_inside_it(tmp_path: Path) -> None:
    """The case's logs must be usable by the audited task's own `inspect_ai`.

    Files arriving in the sandbox is not the claim: the claim is that they are real
    logs. So the solver opens each one inside the container with `read_eval_log` and
    reports what it found.
    """
    source = run_fixture_eval(str(tmp_path / "source"))

    log = eval(
        audit_task(fixture_task(), source),
        model="mockllm/model",
        log_dir=str(tmp_path / "audit"),
        display="none",
    )[0]

    assert log.status == "success", log.error
    assert log.samples is not None and len(log.samples) == 3

    for sample in log.samples:
        readable = sample.store.get("logs_readable")
        assert readable, f"no logs opened in the container for item {sample.id}"
        for entry in readable:
            # Header survived into the container, and the slice holds this item only.
            assert entry["task"] == "fixture_task"
            assert entry["model"] == "mockllm/model"
            assert entry["scorers"] == ["match"]
            assert entry["samples"] == 1
