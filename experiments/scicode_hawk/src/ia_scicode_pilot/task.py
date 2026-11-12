"""A bounded full-auditor pilot using four existing SciCode runs."""

import os
import random

from inspect_ai import Task, task

from inspect_audit import _audit
from inspect_audit._prices import register_prices

BENCHMARK_COMMIT = "369d6ae1de4a7870b4c4119d12bd49a6e484ed64"
BENCHMARK_REPO = "https://github.com/xantheocracy/inspect_evals"
LOGS = ["hawk:scicode-provider-default-pgcjfyx2vbyas1w5"]
LUNA = "openai/gpt-6-luna"
INVESTIGATOR_IMAGE = (
    "ghcr.io/generality-labs/inspect-audit-auditor@"
    "sha256:63665b4948b4adfe9767079ceb9a5d70fe39c0f56518e700a55b0313fa1e52aa"
)
AUDITOR_IMAGE = (
    "ghcr.io/generality-labs/inspect-audit-auditor@"
    "sha256:d21b285bc4ce2f2d34084c2ae382ab5a6996704dbb8d58d1199a1d1925fa71fa"
)


@task
def audit(investigation_package: str) -> Task:
    """Audit SciCode with Luna; investigate eight seeded samples on Hawk."""
    from inspect_evals.scicode.scicode import scicode

    register_prices(
        {f"openrouter/{LUNA}": {"input": 0.10, "output": 0.50, "input_cache_read": 0.01}}
    )
    task_args = {
        "provide_scientific_background": False,
        "timeout": 1800,
        "include_dev_set": False,
        "scaling": False,
        "scaling_token_limit": 200000,
        "feedback": None,
        "stop_on_success": False,
        "scaling_agent": "react",
    }
    dataset = scicode(**task_args).dataset
    ids = sorted((str(s.id) for s in dataset), key=int)
    samples = sorted(random.Random(20261021).sample(ids, 8), key=int)
    return _audit.audit(
        repo=BENCHMARK_REPO,
        revision=BENCHMARK_COMMIT,
        target_task="inspect_evals/scicode",
        source="inspect_evals.scicode.scicode",
        paths=["src/inspect_evals/scicode"],
        logs=LOGS,
        task_args=task_args,
        task_package=f"inspect-evals[scicode] @ git+{BENCHMARK_REPO}@{BENCHMARK_COMMIT}",
        investigation_package=investigation_package,
        box="k8s",
        runs="hawk",
        credential_mode="user",
        hawk_api_url="https://api.hawk.hawk.generalitylabs.ai",
        investigator_image=INVESTIGATOR_IMAGE,
        auditor_image=AUDITOR_IMAGE,
        artifact_dir=(
            f"s3://arcadia-impact-generality-inspect/evals/{os.environ['HAWK_JOB_ID']}/artifacts"
        ),
        investigation_samples=samples,
        checks=["gold-answer", "question-labels"],
        policy={
            "models": {"auditor": [LUNA], "investigators": [LUNA]},
            "inputs": {"logs": LOGS},
            "limits": {"investigation_sample_cost_usd": 0.20},
        },
        instructions=(
            "This is a full-auditor pilot of SciCode. The whole benchmark and four "
            "existing model runs are available. Do not fetch external investigations, corrected "
            "versions, or human defect labels. Those are held out for evaluation. "
            f"The eight sample IDs for detailed investigation are {samples}. "
            "Start the register. Read all four recorded attempts for each selected sample, "
            "and inspect the original problem, reference implementation and tests. "
            "Launch one investigation job for these eight samples with both permitted checks and "
            "the supplied logs, using the fixed task arguments and a $0.20 sample cap. "
            "Do not launch benchmark runs or repeat investigations. Collect the investigation job and "
            "review its findings. Record supported broader findings in the register, "
            "with affected sample/subproblem IDs, explanations and evidence. "
            "Work across all subproblems in the selected samples. Preserve unresolved "
            "work honestly. Full-population numerical analysis is welcome, but distinguish "
            "it from the eight-sample detailed review. Before publishing, copy "
            "/workspace/register.json into /workspace/report/evidence/register.json. "
            "Publish a concise report with concrete reproducible findings, investigation coverage, "
            "tool failures and unresolved claims. Do not require web verification when "
            "the claim can be established by reproducing scientific code/tests."
        ),
    )
