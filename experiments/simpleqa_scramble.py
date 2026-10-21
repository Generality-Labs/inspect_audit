"""Prepare and measure a blinded SimpleQA answer-corruption experiment.

Only run_dir/task is staged for the auditor. The original answers and corruption
map live in run_dir/private, and are used only by the host-side measurement.
"""

import argparse
import hashlib
import json
import random
from collections import Counter
from pathlib import Path

TASK_SOURCE = """from pathlib import Path

from inspect_ai import task, task_with
from inspect_ai.dataset import json_dataset
from inspect_evals.simpleqa.simpleqa import simpleqa

from inspect_audit._task import audit_task
from inspect_audit._prices import register_prices

HERE = Path(__file__).parent


@task
def audit(cost_limit: float = 0.10):
    register_prices({"openrouter/openai/gpt-6-luna": {
        "input": 0.10, "output": 0.50,
        "input_cache_read": 0.01, "input_cache_write": 0.125,
    }})
    benchmark = task_with(simpleqa(), dataset=json_dataset(str(HERE / "benchmark.json")))
    return audit_task(
        benchmark,
        samples="all",
        checks=["gold-answer"],
        judge={"grader": "openrouter/openai/gpt-6-luna"},
        source=str(HERE),
        auditor_image="ghcr.io/generality-labs/inspect-audit-auditor@sha256:63665b4948b4adfe9767079ceb9a5d70fe39c0f56518e700a55b0313fa1e52aa",
        cost_limit=cost_limit,
        message_limit=80,
        time_limit=600,
    )
"""


def prepare(root: Path, seed: int, count: int, corrupt: int) -> None:
    from inspect_evals.simpleqa.dataset import SIMPLEQA_DATASET_SHA256, simpleqa_dataset

    if root.exists():
        raise ValueError(f"refusing to overwrite experiment {root}")
    dataset = list(simpleqa_dataset())
    if not 2 <= corrupt <= count <= len(dataset):
        raise ValueError("need 2 <= corrupted samples <= samples <= dataset size")
    rng = random.Random(seed)
    positions = sorted(rng.sample(range(len(dataset)), count))
    selected = [dataset[i].model_copy(deep=True, update={"id": str(i + 1)}) for i in positions]
    changed = rng.sample(range(count), corrupt)
    answers = [selected[i].target for i in changed]
    # A cyclic permutation preserves the answer distribution and has no fixed
    # points. Check text too: different rows can have identical reference answers.
    for shift in range(1, corrupt):
        replacements = answers[shift:] + answers[:shift]
        if all(
            str(a).strip().casefold() != str(b).strip().casefold()
            for a, b in zip(answers, replacements, strict=True)
        ):
            break
    else:
        raise ValueError("could not permute answers without retaining an original answer")
    truth = {
        str(sample.id): {"original_answer": sample.target, "corrupted": False}
        for sample in selected
    }
    for index, answer in zip(changed, replacements, strict=True):
        sample = selected[index]
        truth[str(sample.id)]["corrupted"] = True
        truth[str(sample.id)]["replacement_answer"] = answer
        selected[index] = sample.model_copy(update={"target": answer})
    task_dir, private = root / "task", root / "private"
    task_dir.mkdir(parents=True)
    private.mkdir(mode=0o700)
    payload = json.dumps([s.model_dump(mode="json", exclude_none=True) for s in selected], indent=2)
    (task_dir / "benchmark.json").write_text(payload)
    (task_dir / "task.py").write_text(TASK_SOURCE)
    (private / "truth.json").write_text(json.dumps(truth, indent=2))
    clean = next(str(s.id) for s in selected if not truth[str(s.id)]["corrupted"])
    broken = str(selected[changed[0]].id)
    manifest = {
        "seed": seed,
        "samples": count,
        "corrupted": corrupt,
        "source_sha256": SIMPLEQA_DATASET_SHA256,
        "modified_dataset_sha256": hashlib.sha256(payload.encode()).hexdigest(),
        "smoke_sample_ids": [clean, broken],
        "auditor": "openrouter/openai/gpt-6-luna",
        "cost_limit_per_sample_usd": 0.10,
    }
    (private / "manifest.json").write_text(json.dumps(manifest, indent=2))
    print(json.dumps({"run_dir": str(root), **manifest}, indent=2))


def measure(root: Path, logs: Path) -> None:
    from inspect_ai.log import list_eval_logs, read_eval_log

    truth = json.loads((root / "private/truth.json").read_text())
    rows = []
    seen = set()
    cost = 0.0
    unpriced_usage = 0
    for info in list_eval_logs(str(logs), recursive=True):
        log = read_eval_log(info.name)
        for sample in log.samples or []:
            for usage in (sample.model_usage or {}).values():
                cost += usage.total_cost or 0.0
                unpriced_usage += int(usage.total_cost is None)
            sid = str(sample.id)
            if sid not in truth:
                raise ValueError(f"unexpected sample {sid}")
            if sid in seen:
                raise ValueError(f"multiple attempts for sample {sid}; choose one run's logs")
            seen.add(sid)
            score = (sample.scores or {}).get("gold-answer")
            grade = str(score.value) if score else "NO_VERDICT"
            rows.append(
                {
                    "sample_id": sid,
                    "corrupted": truth[sid]["corrupted"],
                    "grade": grade,
                    "error": sample.error.message if sample.error else None,
                    "explanation": score.explanation if score else None,
                    "evidence": (score.metadata or {}).get("evidence", []) if score else [],
                    "eval_id": log.eval.eval_id,
                    "epoch": sample.epoch,
                }
            )
    broken = [r for r in rows if r["corrupted"]]
    clean = [r for r in rows if not r["corrupted"]]
    total_broken = sum(t["corrupted"] for t in truth.values())
    missing = sorted(set(truth) - seen)
    result = {
        "population": len(truth),
        "logged": len(rows),
        "missing": missing,
        "corrupted_total": total_broken,
        "corrupted_logged": len(broken),
        "incorrect_on_corrupted": sum(r["grade"] == "INCORRECT" for r in broken),
        "flagged_on_corrupted": sum(r["grade"] in ("INCORRECT", "ALTERNATIVES") for r in broken),
        "flagged_on_unchanged": sum(r["grade"] in ("INCORRECT", "ALTERNATIVES") for r in clean),
        "unresolved": sum(r["grade"] in ("UNVERIFIABLE", "NO_VERDICT") for r in rows),
        "errored": sum(bool(r["error"]) for r in rows),
        "recorded_cost_usd": cost,
        "unpriced_usage_entries": unpriced_usage,
        "corrupted_grades": dict(Counter(r["grade"] for r in broken)),
        "unchanged_grades": dict(Counter(r["grade"] for r in clean)),
        "complete": len(seen) == len(truth),
        "strict_recall": (
            sum(r["grade"] == "INCORRECT" for r in broken) / total_broken
            if len(seen) == len(truth)
            else None
        ),
        "rows": rows,
    }
    output = logs / "measurement.json"
    output.write_text(json.dumps(result, indent=2))
    print(
        json.dumps(
            {k: v for k, v in result.items() if k not in ("rows", "missing")}
            | {"missing_count": len(missing)},
            indent=2,
        )
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    prep = commands.add_parser("prepare")
    prep.add_argument("run_dir", type=Path)
    prep.add_argument("--seed", type=int, default=20261021)
    prep.add_argument("--samples", type=int, default=200)
    prep.add_argument("--corrupt", type=int, default=40)
    score = commands.add_parser("measure")
    score.add_argument("run_dir", type=Path)
    score.add_argument("logs", type=Path)
    args = parser.parse_args()
    if args.command == "prepare":
        prepare(args.run_dir.resolve(), args.seed, args.samples, args.corrupt)
    else:
        measure(args.run_dir.resolve(), args.logs.resolve())


if __name__ == "__main__":
    main()
