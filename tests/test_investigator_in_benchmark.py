"""The investigation's sandbox: their compose plus the investigator's box, merged.

What only this file checks, against real Docker and with no model calls: both boxes
come up, theirs stays `default`, and the network split holds (ours has internet,
theirs, declared `network_mode: none`, does not). Grading and rebuilding inside the
sample are exercised through the real tools in test_investigation.
"""

import sys
from pathlib import Path

import pytest
from inspect_ai import eval, task_with
from inspect_ai.scorer import Score, Target, scorer
from inspect_ai.solver import Generate, TaskState, solver
from inspect_ai.util import sandbox
from inspect_ai.util import subprocess as async_subprocess

sys.path.insert(0, str(Path(__file__).parent / "fixtures" / "toy_benchmark"))
import toy

INVESTIGATOR = "investigator"
INTERNET = [
    "python",
    "-c",
    "import urllib.request; urllib.request.urlopen('https://example.com', timeout=8)",
]


def merged_compose(their_compose: Path, out: Path) -> Path:
    """The package's own investigation sandbox: their compose plus the investigator's box."""
    from inspect_audit._compose import investigation_compose

    spec = investigation_compose(their_compose, stage=out)
    assert isinstance(spec.config, str)
    return Path(spec.config)


def test_the_merged_compose_converts_for_k8s(tmp_path: Path) -> None:
    from k8s_sandbox.compose import convert_compose_to_helm_values

    values = convert_compose_to_helm_values(merged_compose(toy.COMPOSE, tmp_path))
    assert set(values["services"]) == {"default", INVESTIGATOR}
    assert values["services"]["default"]["networkIsolated"] is True


@solver
def probe_boxes():
    async def solve(state: TaskState, generate: Generate) -> TaskState:
        async def container_id(service: str) -> str:
            connection = await sandbox(service).connection()
            assert connection.container is not None
            found = await async_subprocess(
                ["docker", "inspect", "-f", "{{.Id}}", connection.container]
            )
            assert found.success, found.stderr
            return found.stdout.strip()

        box = sandbox()
        state.store.set(
            "checks",
            {
                "two_boxes": await container_id("default") != await container_id(INVESTIGATOR),
                "their_default_is_default": "default" in ((await box.connection()).container or ""),
                "investigator_has_internet": (
                    await sandbox(INVESTIGATOR).exec(INTERNET, timeout=30)
                ).success,
                "their_box_is_offline": not (await box.exec(INTERNET, timeout=30)).success,
            },
        )
        return state

    return solve


@scorer(metrics=[])
def checks_scorer():
    async def score(state: TaskState, target: Target) -> Score:
        checks = state.store.get("checks") or {}
        return Score(value="PASS" if checks and all(checks.values()) else "FAIL", metadata=checks)

    return score


@pytest.mark.docker
def test_both_boxes_come_up_and_only_ours_has_internet(tmp_path: Path) -> None:
    investigation = task_with(
        toy.toy(),
        solver=probe_boxes(),
        scorer=checks_scorer(),
        sandbox=("docker", str(merged_compose(toy.COMPOSE, tmp_path))),
        epochs=1,
    )
    [log] = eval(
        investigation, model="mockllm/model", log_dir=str(tmp_path / "logs"), display="none"
    )
    assert log.status == "success", log.error
    for sample in log.samples or []:
        score = next(iter((sample.scores or {}).values()))
        failed = [k for k, v in (score.metadata or {}).items() if not v]
        assert score.value == "PASS", f"sample {sample.id}: {failed}"
