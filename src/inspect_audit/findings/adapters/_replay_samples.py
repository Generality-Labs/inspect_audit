"""An Inspect task whose dataset is a JSONL file of samples dumped by `_dump_task_samples`.

inspect-dataset scans it as `<this file>@replay_samples`, in its own environment, so the scanners
see the eval's samples without inspect-dataset's dependencies entering the eval's environment.
"""

from __future__ import annotations

import json
import os

from inspect_ai import Task, task
from inspect_ai.dataset import MemoryDataset, Sample
from inspect_ai.util import SandboxEnvironmentSpec

SAMPLES_ENV = "INSPECT_AUDIT_SAMPLES_FILE"


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


@task
def replay_samples() -> Task:
    with open(os.environ[SAMPLES_ENV]) as f:
        samples = [_load_sample(line) for line in f if line.strip()]
    return Task(dataset=MemoryDataset(samples))
