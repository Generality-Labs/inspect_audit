"""One end-to-end run under `mockllm`.

The unit tests only assemble tasks, which cannot catch anything that fails while an
eval actually executes -- an unregistered solver, a sandbox that will not build, a
file that will not copy, a log that arrives but will not open. This runs the real
thing, with no model spend.

Requires Docker; deselect with `-m "not docker"`.
"""

import json
from pathlib import Path
from textwrap import dedent

import pytest
from inspect_ai import eval
from inspect_ai.solver import Generate, Solver, TaskState, solver
from inspect_ai.util import sandbox
from test_helpers.logs import fixture_task, run_fixture_eval

from inspect_audit import audit_task
from inspect_audit._item import AUDIT_ROOT
from inspect_audit._registry import audit_probe

pytestmark = pytest.mark.docker

# run inside the sandbox with the audited task's own inspect_ai, to prove the item's
# logs are genuine logs rather than files that merely arrived
READ_LOGS = dedent(f"""
    import glob, json
    from inspect_ai.log import read_eval_log

    out = []
    for file in sorted(glob.glob("{AUDIT_ROOT}/logs/*.eval")):
        log = read_eval_log(file)
        out.append(
            dict(
                file=file,
                task=log.eval.task,
                model=log.eval.model,
                samples=len(log.samples or []),
                scorers=[s.name for s in (log.eval.scorers or [])],
            )
        )
    print(json.dumps(out))
""")


@solver
def probe_sandbox() -> Solver:
    """Read the item filesystem back out of the sandbox, in place of the agent."""

    async def solve(state: TaskState, generate: Generate) -> TaskState:
        listing = await sandbox().exec(["find", AUDIT_ROOT, "-type", "f", "-print"], timeout=60)
        if not listing.success:
            raise RuntimeError(f"could not list {AUDIT_ROOT}: {listing.stderr}")
        state.store.set("audit_files", sorted(listing.stdout.splitlines()))

        probe = await sandbox().exec(["python", "-c", READ_LOGS], timeout=300)
        if not probe.success:
            raise RuntimeError(f"could not read the item logs in the sandbox: {probe.stderr}")
        state.store.set("logs_readable", json.loads(probe.stdout))
        return state

    return solve


def test_a_container_is_spun_out_and_its_logs_open_inside_it(tmp_path: Path) -> None:
    """The item's logs must be usable by the audited task's own `inspect_ai`.

    Uses `probe_sandbox` rather than the auditing agent: this test is about the
    container, and `mockllm` cannot drive a react loop to a submission.
    """
    source = run_fixture_eval(str(tmp_path / "source"))

    log = eval(
        audit_task(fixture_task(), source, solver=probe_sandbox()),
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
            # header survived into the container, and the slice holds this item only
            assert entry["task"] == "fixture_task"
            assert entry["model"] == "mockllm/model"
            assert entry["scorers"] == ["match"]
            assert entry["samples"] == 1


def test_the_benchmark_box_receives_the_samples_own_state(tmp_path: Path) -> None:
    """A benchmark's environment is image plus per-sample state, not image alone."""
    from inspect_ai import Task
    from inspect_ai.dataset import Sample
    from inspect_ai.scorer import match
    from inspect_ai.solver import generate

    compose = tmp_path / "compose.yaml"
    compose.write_text(
        "services:\n  default:\n    image: python:3.12-slim\n"
        "    command: 'sleep infinity'\n    working_dir: /work\n"
    )
    payload = tmp_path / "payload.txt"
    payload.write_text("the sample's own state")

    bench = Task(
        name="probe/bench",
        dataset=[
            Sample(
                input="q",
                target="a",
                id="one",
                files={"payload.txt": str(payload)},
                setup="cp /work/payload.txt /work/setup-ran.txt",
                sandbox=("docker", str(compose)),
            )
        ],
        solver=generate(),
        scorer=match(),
    )

    @solver
    def content_probe() -> Solver:
        async def solve(state: TaskState, generate: Generate) -> TaskState:
            r = await sandbox("benchmark").exec(
                ["cat", "/work/payload.txt", "/work/setup-ran.txt"], timeout=60
            )
            state.store.set("content", r.stdout if r.success else f"FAIL {r.stderr}")
            return state

        return solve

    log = eval(
        audit_task(bench, samples=["one"], solver=content_probe()),
        model="mockllm/model",
        log_dir=str(tmp_path / "audit"),
        display="none",
    )[0]

    assert log.status == "success", log.error
    assert log.samples is not None
    content = log.samples[0].store.get("content")
    assert content == "the sample's own state" * 2, content


def test_a_mirrored_tool_enacts_in_the_box_and_records_the_call(tmp_path: Path) -> None:
    """A benchmark_* tool runs for real in the benchmark box and lands in the attempt."""
    from inspect_ai import Task
    from inspect_ai.dataset import Sample
    from inspect_ai.scorer import match
    from inspect_ai.solver import generate
    from inspect_ai.tool import ToolDef, bash

    from inspect_audit._state import BenchmarkState, benchmark_tools

    compose = tmp_path / "compose.yaml"
    compose.write_text(
        "services:\n  default:\n    image: python:3.12-slim\n"
        "    command: 'sleep infinity'\n    working_dir: /work\n"
    )
    bench = Task(
        name="probe/bench",
        dataset=[Sample(input="q", target="a", id="one", sandbox=("docker", str(compose)))],
        solver=generate(),
        scorer=match(),
    )

    @solver
    def enact_probe() -> Solver:
        async def solve(state: TaskState, generate: Generate) -> TaskState:
            # the mirrored bash tool, exactly as the auditor would hold it
            (mirrored,) = benchmark_tools([ToolDef(bash())], AUDIT_ROOT)
            await mirrored(command="echo enacted > /work/proof.txt")

            proof = await sandbox("benchmark").exec(["cat", "/work/proof.txt"], timeout=60)
            state.store.set("in_box", proof.stdout.strip() if proof.success else f"FAIL {proof.stderr}")

            session = state.store_as(BenchmarkState)
            state.store.set("provenance", session.provenance_mix())
            state.store.set("recorded_call", session.messages[0].message.tool_calls[0].function)
            return state

        return solve

    log = eval(
        audit_task(bench, samples=["one"], solver=enact_probe()),
        model="mockllm/model",
        log_dir=str(tmp_path / "audit"),
        display="none",
    )[0]

    assert log.status == "success", log.error
    assert log.samples is not None
    store = log.samples[0].store
    # the tool really ran in the benchmark box
    assert store.get("in_box") == "enacted"
    # and it was recorded as an authored call with an enacted result
    assert store.get("recorded_call") == "bash"
    assert store.get("provenance") == {"authored": 1, "enacted": 1}


CONCORDANCE_TASK = dedent('''
    from inspect_ai import Task, task
    from inspect_ai.dataset import MemoryDataset, Sample
    from inspect_ai.scorer import match

    @task
    def graded():
        return Task(
            name="graded",
            dataset=MemoryDataset([Sample(id=1, input="say ANSWER", target="ANSWER")]),
            scorer=match(),
        )
''')


def test_concordance_validates_a_faithful_channel(tmp_path: Path) -> None:
    """Replay-regrade over real logs reproduces recorded grades -> validated.

    Uses a file-addressable task, as a real audit does: the probe re-resolves
    the audited task to recover its scorer.
    """
    task_file = tmp_path / "graded_task.py"
    task_file.write_text(CONCORDANCE_TASK)
    spec = f"{task_file}@graded"

    source = eval(spec, model="mockllm/model", log_dir=str(tmp_path / "source"), display="none")[0].location

    log = eval(
        audit_task(spec, source, solver=audit_probe()),
        model="mockllm/model",
        log_dir=str(tmp_path / "audit"),
        display="none",
    )[0]

    assert log.status == "success", log.error
    assert log.samples is not None
    for sample in log.samples:
        checks = sample.store.get("probe")
        # match is deterministic and box-free: our regrade must reproduce every
        # recorded grade, or the channel is unfaithful
        assert checks["concordance"] == "validated", checks.get("concordance_reasons")
