"""Write an Inspect task's samples to JSONL, plus the dataset identity Inspect records.

Run as a standalone script inside the audited checkout's own environment, so the task's loader
runs with the dependencies the eval locks. It must import nothing from inspect_audit, which is
not installed there.

Usage: python _dump_task_samples.py <task spec> <samples.jsonl> <meta.json>
"""

from __future__ import annotations

import json
import sys
from collections.abc import Sequence

from inspect_ai._eval.loader import load_tasks


def main(argv: Sequence[str]) -> None:
    spec, samples_path, meta_path = argv
    tasks = load_tasks([spec])
    if len(tasks) != 1:
        raise SystemExit(f"{spec!r} matched {len(tasks)} tasks; name exactly one")
    dataset = tasks[0].dataset
    count = 0
    with open(samples_path, "w") as out:
        for sample in dataset:
            out.write(sample.model_dump_json() + "\n")
            count += 1
    meta = {
        "task": spec,
        "dataset_name": dataset.name,
        "dataset_location": dataset.location,
        "samples": count,
    }
    with open(meta_path, "w") as out:
        json.dump(meta, out)


if __name__ == "__main__":
    main(sys.argv[1:])
