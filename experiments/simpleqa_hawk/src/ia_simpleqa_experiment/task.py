from pathlib import Path

from inspect_ai import task, task_with
from inspect_ai.dataset import json_dataset
from inspect_evals.simpleqa.simpleqa import simpleqa

from inspect_audit._prices import price_models
from inspect_audit._task import investigation_task

HERE = Path(__file__).parent


@task
def investigation(cost_limit: float = 0.10):
    price_models(["openrouter/openai/gpt-6-luna"])
    benchmark = task_with(simpleqa(), dataset=json_dataset(str(HERE / "benchmark.json")))
    return investigation_task(
        benchmark,
        samples="all",
        checks=["gold-answer"],
        judge={"grader": "openrouter/openai/gpt-6-luna"},
        source=str(HERE),
        investigator_image="ghcr.io/generality-labs/inspect-audit-auditor@sha256:63665b4948b4adfe9767079ceb9a5d70fe39c0f56518e700a55b0313fa1e52aa",
        cost_limit=cost_limit,
        message_limit=80,
        time_limit=600,
    )
