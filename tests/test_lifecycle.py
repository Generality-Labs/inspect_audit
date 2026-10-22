"""A real child audit, collected into the investigator's checkpointed register."""

import asyncio
import json
from pathlib import Path

import pytest
from inspect_ai import Task, eval
from inspect_ai.dataset import Sample
from inspect_ai.solver import Generate, TaskState, solver

from inspect_audit._investigate import Investigation, ModelMenu, jobs
from inspect_audit._register import Register, load, save
from inspect_audit.dispatch import Job, LocalRuns
from inspect_audit.dispatch.policy import InvestigationAuthorizer, LaunchPolicy, Models, canonical


@pytest.mark.docker
def test_child_audit_is_collected_once_into_the_register(tmp_path: Path) -> None:
    pytest.importorskip("ia_compatibility.probe")
    fixtures = Path(__file__).parent / "fixtures"
    root = tmp_path / "investigation"
    root.mkdir()
    compose = tmp_path / "parent.yaml"
    compose.write_text(
        "services:\n  default:\n    image: python:3.12-slim\n    command: sleep infinity\n    working_dir: /workspace\n"
    )
    policy = LaunchPolicy(
        task="toy",
        task_package="fixture",
        audit_package="inspect_audit",
        auditor_image="python:3.12-slim",
        models=Models(auditors=("ia_probe/driver",)),
        checks=("gold-answer",),
        environment_json=canonical({"PYTHONPATH": str(fixtures.resolve())}),
        configuration_json="{}",
    )
    inv = Investigation(
        root=root,
        runs=LocalRuns(tmp_path / "children"),
        where="local",
        task="toy",
        task_package="fixture",
        audit_package="inspect_audit",
        auditor_image="python:3.12-slim",
        menu=ModelMenu(auditors=["ia_probe/driver"], subjects=[], judges=[]),
        supplied=[],
        authorizer=InvestigationAuthorizer(policy),
    )

    @solver
    def coordinate():
        async def solve(state: TaskState, generate: Generate) -> TaskState:
            await save(
                Register(
                    subject={"task": "toy"},
                    checks=["gold-answer"],
                    samples={"a": {"gold-answer": []}},
                )
            )
            job = Job(
                label="child",
                task="inspect_audit/audit",
                package="inspect_audit",
                extra_packages=["fixture"],
                models=["ia_probe/driver"],
                task_args={
                    "task": "toy",
                    "task_args": {},
                    "samples": ["a"],
                    "checks": ["gold-answer"],
                    "source": "toy_benchmark.toy",
                    "judge": {},
                    "auditor_image": "python:3.12-slim",
                    "cost_limit": 0.01,
                    "model_prices": {"ia_probe/driver": {"input": 1, "output": 1}},
                },
            )
            await inv.launch(job, "audit", "API-free lifecycle test")
            manage = jobs(inv)
            try:
                await asyncio.wait_for(manage("wait", "child", 3), timeout=190)
                first = await manage("collect", "child", None)
                second = await manage("collect", "child", None)
                takes = load().samples["a"]["gold-answer"]
                assert "1 auditor take(s) added" in first
                assert "0 auditor take(s) added" in second
                assert len(takes) == 1 and takes[0].label == "UNVERIFIABLE"
                assert takes[0].by == "auditor" and takes[0].evidence
                state.metadata["register"] = load().model_dump(mode="json")
                state.metadata["jobs"] = json.loads(inv.record().model_dump_json())
            finally:
                await manage("stop", "child", None)
            return state

        return solve

    [log] = eval(
        Task(
            dataset=[Sample(input="coordinate")],
            solver=coordinate(),
            sandbox=("docker", str(compose)),
        ),
        model="mockllm/model",
        log_dir=str(tmp_path / "logs"),
        display="none",
    )
    assert log.status == "success", log.error
    assert log.samples and log.samples[0].metadata["register"]["samples"]["a"]["gold-answer"]
