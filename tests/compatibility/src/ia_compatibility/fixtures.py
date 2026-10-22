"""Small real Inspect benchmarks for execution contracts, with deterministic graders."""

import tempfile
from pathlib import Path

import yaml
from inspect_ai import Task, task
from inspect_ai.dataset import Sample
from inspect_ai.scorer import CORRECT, INCORRECT, Score, Scorer, Target, accuracy, scorer
from inspect_ai.solver import TaskState, generate
from inspect_ai.util import sandbox


@scorer(metrics=[accuracy()])
def native_environment() -> Scorer:
    async def score(state: TaskState, target: Target) -> Score:
        prepared = await sandbox().read_file("/work/setup.txt")
        supplied = await sandbox().read_file("/work/input.txt")
        helper = await sandbox("helper").read_file("/work/ready.txt")
        environment = await sandbox().exec(["sh", "-c", 'printf "%s" "$FIXTURE_MODE"'])
        if (prepared.strip(), supplied.strip(), helper.strip(), environment.stdout) != (
            "prepared",
            "question",
            "helper-ready",
            "native-k8s",
        ):
            raise RuntimeError(
                "native fixture files, setup, named service or environment were lost"
            )
        return Score(value=CORRECT if state.output.completion.strip() == target.text else INCORRECT)

    return score


@task
def native_k8s() -> Task:
    """Native Helm values, two named services, sample files/setup and a real grader."""
    root = Path(tempfile.mkdtemp(prefix="ia-native-k8s-"))
    path = root / "values.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "services": {
                    "default": {
                        "image": "python:3.12-slim",
                        "command": ["sh", "-c", "mkdir -p /work; exec tail -f /dev/null"],
                        "workingDir": "/work",
                        "readinessProbe": {"exec": {"command": ["test", "-d", "/work"]}},
                        "env": [{"name": "FIXTURE_MODE", "value": "native-k8s"}],
                        "resources": {
                            "requests": {"cpu": "100m", "memory": "128Mi"},
                            "limits": {"cpu": "500m", "memory": "512Mi"},
                        },
                    },
                    "helper": {
                        "image": "python:3.12-slim",
                        "command": [
                            "sh",
                            "-c",
                            "mkdir -p /work; printf helper-ready > /work/ready.txt; exec tail -f /dev/null",
                        ],
                        "readinessProbe": {"exec": {"command": ["test", "-f", "/work/ready.txt"]}},
                        "resources": {
                            "requests": {"cpu": "100m", "memory": "128Mi"},
                            "limits": {"cpu": "500m", "memory": "512Mi"},
                        },
                    },
                },
                "allowDomains": [],
                "allowEntities": [],
            },
            sort_keys=False,
        )
    )
    return Task(
        dataset=[
            Sample(
                id="native",
                input="question",
                target="42",
                files={"/work/input.txt": "question"},
                setup="printf prepared > /work/setup.txt",
            )
        ],
        solver=generate(),
        scorer=native_environment(),
        sandbox=("k8s", str(path)),
    )
