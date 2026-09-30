"""An Inspect task whose dataset is a JSONL file of samples dumped by `_dump_task_samples`.

inspect-dataset scans it as `<this file>@replay_samples`, in its own environment, so the scanners
see the eval's samples without inspect-dataset's dependencies entering the eval's environment.
"""

from __future__ import annotations

import os

from inspect_ai import Task, task
from inspect_ai.dataset import MemoryDataset, Sample

SAMPLES_ENV = "INSPECT_AUDIT_SAMPLES_FILE"


@task
def replay_samples() -> Task:
    with open(os.environ[SAMPLES_ENV]) as f:
        samples = [Sample.model_validate_json(line) for line in f if line.strip()]
    return Task(dataset=MemoryDataset(samples))
