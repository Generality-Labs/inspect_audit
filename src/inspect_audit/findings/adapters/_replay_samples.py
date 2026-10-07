"""An Inspect task whose dataset is a JSONL file of samples dumped by `_dump_task_samples`.

inspect-dataset scans it as `<this file>@replay_samples`, in its own environment, so the scanners
see the eval's samples without inspect-dataset's dependencies entering the eval's environment.
"""

from __future__ import annotations

import json
import os

from inspect_ai import Task, task
from inspect_ai._util.registry import RegistryInfo, registry_add
from inspect_ai.dataset import MemoryDataset, Sample
from inspect_ai.scorer import Score, Scorer, Target
from inspect_ai.solver import TaskState
from inspect_ai.util import SandboxEnvironmentSpec

SAMPLES_ENV = "INSPECT_AUDIT_SAMPLES_FILE"
SCORERS_ENV = "INSPECT_AUDIT_SCORERS"  # comma-separated registry names of the eval's scorers


def _load_sample(line: str) -> Sample:
    """A Sample from its JSON, keeping the sandbox.

    `Sample.__init__` passes `sandbox` through `resolve_sandbox_environment`, which accepts a
    string, spec or tuple but turns the serialised dict into None, so it is restored separately.
    """
    data = json.loads(line)
    sandbox = data.pop("sandbox", None)
    sample = Sample.model_validate(data)
    if sandbox is not None:
        sample.sandbox = SandboxEnvironmentSpec.model_validate(sandbox)
    return sample


def _stub_scorer(name: str) -> Scorer:
    """A scorer that never scores, registered under the eval's scorer name.

    inspect-dataset reads scorer names off the task to decide whether its text-comparison scanners
    apply. The eval's own scorers are not importable here, so the name is all that is carried.
    """

    async def score(state: TaskState, target: Target) -> Score:
        raise NotImplementedError(f"{name} is a stand-in for the eval's scorer; it never scores")

    registry_add(score, RegistryInfo(type="scorer", name=name))
    return score


@task
def replay_samples() -> Task:
    with open(os.environ[SAMPLES_ENV]) as f:
        samples = [_load_sample(line) for line in f if line.strip()]
    names = [n for n in os.environ.get(SCORERS_ENV, "").split(",") if n]
    scorers = [_stub_scorer(name) for name in names] or None
    return Task(dataset=MemoryDataset(samples), scorer=scorers)
