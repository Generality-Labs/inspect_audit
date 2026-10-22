from pathlib import Path

from inspect_ai import task, task_with
from inspect_ai.dataset import json_dataset
from inspect_evals.simpleqa.simpleqa import simpleqa

from inspect_audit._prices import register_prices
from inspect_audit._task import audit_task

HERE = Path(__file__).parent


@task
def audit(cost_limit: float = 0.10):
    register_prices(
        {
            "openrouter/openai/gpt-6-luna": {
                "input": 0.10,
                "output": 0.50,
                "input_cache_read": 0.01,
                "input_cache_write": 0.125,
            }
        }
    )
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
