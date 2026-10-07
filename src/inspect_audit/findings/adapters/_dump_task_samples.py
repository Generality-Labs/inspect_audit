"""Write an Inspect task's samples to JSONL, plus the dataset identity Inspect records.

Run as a standalone script inside the audited checkout's own environment, so the task's loader
runs with the dependencies the eval locks. It must import nothing from inspect_audit, which is
not installed there. Metadata JSON cannot hold is written as a native equivalent (numpy scalars and
arrays) or as its str(), so an unusual value degrades rather than failing the whole eval.

Usage: python _dump_task_samples.py <task spec> <samples.jsonl> <meta.json>
"""

from __future__ import annotations

import json
import sys
from collections.abc import Sequence
from typing import Any

from inspect_ai._eval.loader import load_tasks


def _json_fallback(value: Any) -> Any:
    """A JSON-safe stand-in for a value pydantic cannot serialise, such as a numpy scalar.

    numpy is duck-typed rather than imported, because the eval's environment may not have it.
    """
    if getattr(value, "ndim", None) == 0 and callable(getattr(value, "item", None)):
        return value.item()
    if callable(getattr(value, "tolist", None)):
        return value.tolist()
    return str(value)


def _scorer_names(task: Any) -> list[str]:
    """Registry names of the task's scorers, as inspect-dataset would read them off the task itself.

    The replay task declares these names so inspect-dataset's scorer-aware scanners (answer_length,
    inconsistent_format) judge applicability by the eval's scorer, not by the stand-in's absence of one.
    """
    from inspect_ai._util.registry import is_registry_object, registry_info

    scorer = getattr(task, "scorer", None)
    if scorer is None:
        return []
    scorers = scorer if isinstance(scorer, list | tuple) else [scorer]
    return [registry_info(s).name for s in scorers if is_registry_object(s)]


def main(argv: Sequence[str]) -> None:
    spec, samples_path, meta_path = argv
    tasks = load_tasks([spec])
    if len(tasks) != 1:
        raise SystemExit(f"{spec!r} matched {len(tasks)} tasks; name exactly one")
    dataset = tasks[0].dataset
    count = 0
    with open(samples_path, "w") as out:
        for sample in dataset:
            out.write(sample.model_dump_json(fallback=_json_fallback) + "\n")
            count += 1
    meta = {
        "task": spec,
        "dataset_name": dataset.name,
        "dataset_location": dataset.location,
        "samples": count,
        "scorers": _scorer_names(tasks[0]),
    }
    with open(meta_path, "w") as out:
        json.dump(meta, out)


if __name__ == "__main__":
    main(sys.argv[1:])
