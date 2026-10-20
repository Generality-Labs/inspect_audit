"""Derisker for the port: run the benchmark's own task with the auditor as its solver.

Proves, against a real Docker sandbox and with no model calls, the four things the
port rests on:

1. their box and ours both come up, their `default` stays the default, and their
   sample files, setup script and `setup=` solvers all ran;
2. our box has internet and theirs (`network_mode: none`) does not;
3. `grade` -- a copy of the live state with an edited transcript, handed to their
   scorer -- uses their real grader against their live box;
4. a full rebuild of their box (force-recreate, re-lay files, re-run setup)
   restores a box the auditor broke, and leaves ours untouched.

Their scorer must never grade the auditor itself: Inspect's end-of-sample scoring
runs our scorer only.
"""

import subprocess
import sys
from copy import copy
from pathlib import Path
from typing import Any

import pytest
import yaml
from inspect_ai import Task, eval, task_with
from inspect_ai.dataset import Sample
from inspect_ai.model import ChatMessageAssistant, ModelOutput
from inspect_ai.scorer import Score, Scorer, Target, scorer
from inspect_ai.solver import Generate, TaskState, solver
from inspect_ai.util import sandbox
from inspect_ai.util import subprocess as async_subprocess

sys.path.insert(0, str(Path(__file__).parent / "fixtures" / "toy_benchmark"))
import toy

pytestmark = pytest.mark.docker

AUDITOR = "auditor"
INTERNET = [
    "python",
    "-c",
    "import urllib.request; urllib.request.urlopen('https://example.com', timeout=8)",
]


def wrap_compose(their_compose: Path, out: Path) -> Path:
    """Their compose plus an `auditor` service on its own network, flattened by Docker."""
    wrapper = {
        "include": [str(their_compose)],
        "services": {
            AUDITOR: {
                "image": "python:3.12-slim",
                "command": "tail -f /dev/null",
                "init": True,
                "networks": ["auditor_net"],
            }
        },
        "networks": {"auditor_net": {}},
    }
    (out / "wrapper.yaml").write_text(yaml.safe_dump(wrapper, sort_keys=False))
    flat = subprocess.run(
        ["docker", "compose", "-f", str(out / "wrapper.yaml"), "config"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    merged = yaml.safe_load(flat)
    # names Docker derives from the project: the project is Inspect's to name, and
    # the k8s converter rejects a network carrying one
    merged.pop("name", None)
    for network in (merged.get("networks") or {}).values():
        if isinstance(network, dict):
            network.pop("name", None)
    path = out / "compose.yaml"
    path.write_text(yaml.safe_dump(merged, sort_keys=False))
    return path


async def grade(state: TaskState, scorers: list[Scorer], answer: str) -> list[Score]:
    """Their scorers on a copy of the live state with the answer as the completion."""
    graded = copy(state)
    graded.messages = [*state.messages, ChatMessageAssistant(content=answer)]
    graded.output = ModelOutput.from_content(model="auditor/authored", content=answer)
    return [await s(graded, state.target) for s in scorers]


async def container_id(service: str) -> str:
    connection = await sandbox(service).connection()
    assert connection.container is not None
    result = await async_subprocess(["docker", "inspect", "-f", "{{.Id}}", connection.container])
    assert result.success, result.stderr
    return result.stdout.strip()


async def rebuild(their_sample: Sample) -> None:
    """Force-recreate their default box, then re-lay files and re-run setup."""
    from inspect_ai.util._sandbox.docker.docker import DockerSandboxEnvironment

    project = sandbox().as_type(DockerSandboxEnvironment)._project  # pyright: ignore[reportPrivateUsage]
    assert project.config is not None
    recreated = await async_subprocess(
        [
            "docker",
            "compose",
            "-p",
            project.name,
            "-f",
            project.config,
            "up",
            "-d",
            "--force-recreate",
            "--no-deps",
            "--wait",
            "default",
        ]
    )
    assert recreated.success, recreated.stderr
    for path, contents in (their_sample.files or {}).items():
        await sandbox().write_file(path, contents)
    if their_sample.setup:
        result = await sandbox().exec(["bash", "-c", their_sample.setup])
        assert result.success, result.stderr


@solver
def derisk(scorers: list[Scorer], samples: dict[str, Sample]):
    async def solve(state: TaskState, generate: Generate) -> TaskState:
        checks: dict[str, Any] = {}
        box = sandbox()

        # 1. both boxes, theirs is default, their files/setup/setup-solver ran
        seeded = await box.exec(["cat", "/work/input.txt", "/work/setup_done"])
        checks["files_and_setup"] = seeded.success and "ready" in seeded.stdout
        checks["their_setup_solver"] = bool(state.store.get("their_setup_ran"))
        theirs, ours = await container_id("default"), await container_id(AUDITOR)
        checks["two_boxes"] = theirs != ours
        default_name = (await box.connection()).container or ""
        checks["their_default_is_default"] = "default" in default_name

        # 2. network: ours has internet, theirs does not
        checks["auditor_internet"] = (await sandbox(AUDITOR).exec(INTERNET, timeout=30)).success
        checks["their_box_offline"] = not (await box.exec(INTERNET, timeout=30)).success

        # 3. grade with their real grader against their live box
        right = (await grade(state, scorers, state.target.text))[0].value
        wrong = (await grade(state, scorers, "nope"))[0].value
        checks["grade_right_is_C"] = right == "C"
        checks["grade_wrong_is_I"] = wrong == "I"

        # break their box: the grader must see it
        await box.exec(["rm", "-rf", "/work"])
        checks["grade_sees_broken_box"] = (await grade(state, scorers, state.target.text))[
            0
        ].value == "I"

        # 4. full rebuild restores it; ours untouched
        await rebuild(samples[str(state.sample_id)])
        checks["rebuilt_new_container"] = await container_id("default") != theirs
        checks["auditor_untouched"] = await container_id(AUDITOR) == ours
        checks["grade_after_rebuild_is_C"] = (await grade(state, scorers, state.target.text))[
            0
        ].value == "C"

        state.store.set("checks", checks)
        return state

    return solve


@scorer(metrics=[])
def checks_scorer():
    async def score(state: TaskState, target: Target) -> Score:
        checks = state.store.get("checks") or {}
        return Score(value="PASS" if checks and all(checks.values()) else "FAIL", metadata=checks)

    return score


def test_the_flattened_compose_converts_for_k8s(tmp_path: Path) -> None:
    from k8s_sandbox.compose import convert_compose_to_helm_values

    values = convert_compose_to_helm_values(wrap_compose(toy.COMPOSE, tmp_path))
    assert set(values["services"]) == {"default", AUDITOR}
    assert values["services"]["default"]["networkIsolated"] is True


def test_auditor_runs_inside_the_benchmark_task(tmp_path: Path) -> None:
    their = toy.toy()
    assert isinstance(their.scorer, list)
    their_scorers = list(their.scorer)
    samples = {str(s.id): s for s in toy.samples()}
    flat = wrap_compose(toy.COMPOSE, tmp_path)

    audit: Task = task_with(
        their,
        solver=derisk(their_scorers, samples),
        scorer=checks_scorer(),
        sandbox=("docker", str(flat)),
        message_limit=None,
        token_limit=None,
        turn_limit=None,
        time_limit=None,
        working_limit=None,
        cost_limit=None,
        epochs=1,
    )
    toy.SCORER_CALLS.clear()
    [log] = eval(audit, model="mockllm/model", log_dir=str(tmp_path / "logs"), display="none")

    assert log.status == "success", log.error
    assert log.samples is not None and len(log.samples) == 2
    for sample in log.samples:
        assert sample.error is None, sample.error
        score = next(iter((sample.scores or {}).values()))
        failed = [k for k, v in (score.metadata or {}).items() if not v]
        assert score.value == "PASS", f"sample {sample.id} failed: {failed} ({score.metadata})"
    # their grader ran only for our four grade calls per sample, never on the auditor
    assert sorted(toy.SCORER_CALLS) == sorted(["a"] * 4 + ["b"] * 4)
