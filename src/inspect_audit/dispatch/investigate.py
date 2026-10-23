r"""A Hawk eval-set config for investigating samples of a benchmark run, written for you.

  python -m inspect_audit.dispatch.investigate \\
      --benchmark https://github.com/UKGovernmentBEIS/inspect_evals@<commit> --extra swe_bench \\
      --task inspect_evals/swe_bench --task-args args.yaml --source inspect_evals.swe_bench.swe_bench \\
      --samples django__django-10880 django__django-10097 --checks insufficiently-specified \\
      --logs hawk:<eval set id> --model openrouter/openai/gpt-6-luna --cost-limit 0.3 \\
      --one-job-per-sample > investigate.eval-set.yaml

What you declare is what the investigation is: the benchmark (repository, commit,
extra), the task and its arguments, the samples, checks, logs, judges and model.
Everything else is plumbing and is filled in the way the auditor's own child jobs are:
this package at its current commit, the Inspect version jobs run on, the benchmark's
extra pinned to its own lockfile at that commit, Hawk's client when logs come from
Hawk, and Hawk's address for fetching them.
"""

import argparse
import sys
from pathlib import Path
from typing import Any

import yaml

from .hawk import hawk_config
from .jobs import Job
from .packages import locked_extra_pins, own_package_spec


def investigation_config(
    *,
    benchmark: str,
    extra: str | None,
    task: str,
    task_args: dict[str, Any],
    source: str,
    samples: list[str],
    checks: list[str],
    logs: list[str],
    judges: dict[str, str],
    model: str,
    cost_limit: float | None,
    label: str,
    one_job_per_sample: bool,
    hawk_api_url: str | None,
    package: str | None = None,
) -> dict[str, Any]:
    """The eval-set config: one item, or one per sample for per-sample sandboxes."""
    repo, at, commit = benchmark.rpartition("@")
    if not at or not ("://" in repo or repo.startswith("git@")) or len(commit) < 7:
        raise ValueError("--benchmark is REPO@COMMIT, e.g. https://github.com/org/evals@<sha>")
    name = task.partition("/")[0].replace("_", "-")
    spec = f"{name}[{extra}] @ git+{repo}@{commit}" if extra else f"{name} @ git+{repo}@{commit}"
    pins = locked_extra_pins(repo, commit, extra) if extra else []
    ours = package or own_package_spec()
    if ours is None:
        raise ValueError("cannot tell which inspect_audit commit to install: pass --package")

    def job(selected: list[str]) -> Job:
        args: dict[str, Any] = {
            "task": task,
            "samples": selected,
            "checks": checks,
            "task_args": task_args,
            "source": source,
            "judge": judges,
        }
        if logs:
            args["logs"] = logs
        if cost_limit is not None:
            args["cost_limit"] = cost_limit
        return Job(
            label=label,
            task="inspect_audit/investigation",
            package=ours,
            models=[model],
            task_args=args,
            extra_packages=[spec, *pins],
        )

    groups = [[s] for s in samples] if one_job_per_sample else [samples]
    configs = [hawk_config(job(group)) for group in groups]
    config = configs[0]
    config["tasks"][0]["items"] = [c["tasks"][0]["items"][0] for c in configs]
    if any(log.startswith("hawk:") for log in logs):
        if not hawk_api_url:
            raise ValueError("hawk: logs are fetched from Hawk: set HAWK_API_URL")
        # Hawk does not tell a job where its own API is
        config["runner"]["environment"]["HAWK_API_URL"] = hawk_api_url
    return config


def _hawk_api_url() -> str | None:
    """Hawk's API as the hawk CLI is configured: the environment, then its env file."""
    from hawk.cli.config import CliConfig

    return CliConfig().api_url or None


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    parser.add_argument("--benchmark", required=True, help="REPO@COMMIT the logs were made with")
    parser.add_argument("--extra", help="the benchmark package's extra, e.g. swe_bench")
    parser.add_argument("--task", required=True, help="registry name, e.g. inspect_evals/gpqa")
    parser.add_argument("--task-args", help="YAML file of the run's task arguments")
    parser.add_argument("--source", required=True, help="module or path of the task's code")
    parser.add_argument("--samples", nargs="+", required=True)
    parser.add_argument("--checks", nargs="+", required=True)
    parser.add_argument("--logs", nargs="*", default=[], help="hawk:<id>, paths or URLs")
    parser.add_argument("--judge", nargs="*", default=[], help="role=model, e.g. grader=...")
    parser.add_argument("--model", required=True, help="the investigator's model")
    parser.add_argument("--cost-limit", type=float)
    parser.add_argument("--label", default="investigation")
    parser.add_argument("--one-job-per-sample", action="store_true")
    parser.add_argument("--package", help="inspect_audit pip spec, if not this checkout")
    a = parser.parse_args(argv)
    task_args = yaml.safe_load(Path(a.task_args).read_text()) if a.task_args else {}
    judges = dict(j.split("=", 1) for j in a.judge)
    config = investigation_config(
        benchmark=a.benchmark,
        extra=a.extra,
        task=a.task,
        task_args=task_args or {},
        source=a.source,
        samples=a.samples,
        checks=a.checks,
        logs=a.logs,
        judges=judges,
        model=a.model,
        cost_limit=a.cost_limit,
        label=a.label,
        one_job_per_sample=a.one_job_per_sample,
        hawk_api_url=_hawk_api_url(),
        package=a.package,
    )
    yaml.safe_dump(config, sys.stdout, sort_keys=False, width=200)


if __name__ == "__main__":
    main()
