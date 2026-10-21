"""Small full-investigator runs across different Inspect tasks."""

import importlib
import json
import os
import random

from inspect_ai import Task, task

from inspect_audit._investigate import investigate
from inspect_audit._prices import register_prices

REPO = "https://github.com/xantheocracy/inspect_evals"
REVISION = "369d6ae1de4a7870b4c4119d12bd49a6e484ed64"
LUNA = "openai/gpt-6-luna"
AUDITOR_IMAGE = "ghcr.io/generality-labs/inspect-audit-auditor@sha256:63665b4948b4adfe9767079ceb9a5d70fe39c0f56518e700a55b0313fa1e52aa"
INVESTIGATOR_IMAGE = "ghcr.io/generality-labs/inspect-audit-auditor@sha256:d21b285bc4ce2f2d34084c2ae382ab5a6996704dbb8d58d1199a1d1925fa71fa"


@task
def investigation(benchmark: str, audit_package: str) -> Task:
    """Run the investigator and one two-sample audit on Hawk."""
    specifications = {
        "scicode": {
            "provide_scientific_background": False,
            "timeout": 1800,
            "include_dev_set": False,
            "scaling": False,
            "scaling_token_limit": 200000,
            "feedback": None,
            "stop_on_success": False,
            "scaling_agent": "react",
        },
        "simpleqa": {"scorer": "tool", "grade_parse": "strict"},
        "gsm8k": {"fewshot": 0},
        "bbh": {"subset_name": "date_understanding", "prompt_type": "answer_only"},
        "agentharm": {
            "behavior_ids": [
                "9-2",
                "21-2",
                "24-2",
                "25-2",
                "28-2",
                "29-2",
                "33-2",
                "38-2",
                "39-2",
                "43-2",
                "61-2",
                "66-2",
            ],
            "refusal_judge": f"openrouter/{LUNA}",
            "semantic_judge": f"openrouter/{LUNA}",
        },
    }
    args = specifications[benchmark]
    source = f"inspect_evals.{benchmark}.{benchmark}"
    benchmark_task = getattr(importlib.import_module(source), benchmark)(**args)
    ids = [str(s.id if s.id is not None else i + 1) for i, s in enumerate(benchmark_task.dataset)]
    samples = random.Random(20261021).sample(ids, 2)
    logs = ["hawk:scicode-provider-default-pgcjfyx2vbyas1w5"] if benchmark == "scicode" else []
    judges = {"grader": LUNA} if benchmark == "simpleqa" else {}
    register_prices(
        {
            f"openrouter/{LUNA}": {
                "input": 0.10,
                "output": 0.50,
                "input_cache_read": 0.01,
            }
        }
    )
    return investigate(
        repo=REPO,
        revision=REVISION,
        target_task=f"inspect_evals/{benchmark}",
        source=source,
        paths=[f"src/inspect_evals/{benchmark}"],
        logs=logs,
        task_args=args,
        task_package=f"inspect-evals[scicode,agentharm] @ git+{REPO}@{REVISION}",
        audit_package=audit_package,
        box="k8s",
        runs="hawk",
        credential_mode="user",
        hawk_api_url="https://api.hawk.hawk.generalitylabs.ai",
        auditor_image=AUDITOR_IMAGE,
        investigator_image=INVESTIGATOR_IMAGE,
        artifact_dir=f"s3://arcadia-impact-generality-inspect/evals/{os.environ['HAWK_JOB_ID']}/artifacts",
        audit_samples=samples,
        checks=["gold-answer", "question-labels"],
        policy={
            "models": {"investigator": [LUNA], "auditors": [LUNA], "judges": [LUNA]},
            "inputs": {"logs": logs},
            "limits": {"audit_sample_cost_usd": 0.20},
        },
        instructions=(
            f"Functionality smoke run of {benchmark}, not an accuracy evaluation. "
            f"Start the register. Launch exactly one audit job for sample IDs {samples}, "
            "with both gold-answer and question-labels, Luna as auditor, and a $0.20 "
            f"per-sample limit. source={source}; task_args={json.dumps(args)}; "
            f"judge={json.dumps(judges)}. Use all supplied logs, or [] if none supplied. "
            "The whole declared dataset is available; detailed audit covers only these "
            "two samples. Do not launch benchmark jobs or repeat audits. Do not fetch "
            "external audits or correction labels. Wait for the child, collect it, "
            "and inspect both samples' verdicts. Record investigator reviews and "
            "supported findings. Copy /workspace/register.json to "
            "/workspace/report/evidence/register.json. Try to publish a concise report. "
            "Record concrete tool, grading, collection and publication failures with "
            "their error text. If publication is blocked, preserve the register and "
            "report sources, state the blocker, and finish; do not spend the budget "
            "repeating the same failed validation. Prioritise completing the end-to-end "
            "path and describing what broke over exhaustive scientific analysis."
        ),
    )
