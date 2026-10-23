"""Inspect model identities, settings, pricing and launch permissions round-trip."""

import asyncio
import time
from pathlib import Path

import pytest
from inspect_ai.log import read_eval_log
from inspect_ai.model import GenerateConfig, ModelConfig, ModelCost, ModelInfo
from inspect_ai.tool import ToolError
from inspect_ai.util import Store

from inspect_audit._prices import prices_for_models
from inspect_audit.dispatch import COMPLETE, FINISHED, Job, LocalRuns, hawk_config
from inspect_audit.dispatch.policy import (
    AuditAuthorizer,
    LaunchPolicy,
    Models,
    PolicyStore,
    canonical,
    ensure_model,
)


@pytest.mark.parametrize(
    "name",
    ["openai/gpt-4o-mini", "anthropic/claude-haiku-4-5", "openrouter/anthropic/claude-haiku-4-5"],
)
def test_hawk_uses_inspects_full_model_identity(name: str) -> None:
    from hawk.core.types import EvalSetConfig

    configured = ModelConfig(
        model=name,
        config=GenerateConfig(temperature=0.2, max_tokens=321),
        base_url="https://gateway.example/v1",
        args={"timeout": 45},
    )
    config = EvalSetConfig.model_validate(
        hawk_config(
            Job(
                label="native",
                task="bench/task",
                package="bench",
                models=[name],
                model_roles={"grader": name},
                model_configs={name: configured},
            )
        )
    )
    assert "inspect-ai==0.3.266" in config.packages
    [group] = config.models
    assert group.package == "inspect-ai"
    [item] = group.items
    assert item.name == name
    assert item.args.base_url == configured.base_url
    assert item.args.parsed_config.temperature == 0.2
    assert item.args.parsed_config.max_tokens == 321
    assert config.model_roles["grader"].items[0] == item


def test_direct_and_router_routes_are_distinct_permissions() -> None:
    policy = LaunchPolicy(
        task=None,
        task_package=None,
        investigation_package=None,
        investigator_image="image",
        models=Models(auditor=("openrouter/openai/model",)),
        checks=(),
        configuration_json="{}",
    )
    with pytest.raises(RuntimeError, match="outside"):
        ensure_model(policy, PolicyStore(store=Store()), "openai/model")
    ensure_model(policy, PolicyStore(store=Store()), "openrouter/openai/model")


def test_model_settings_are_frozen_and_applied_by_the_authorizer() -> None:
    name = "mockllm/model"
    configured = ModelConfig(model=name, config=GenerateConfig(temperature=0.2))
    policy = LaunchPolicy(
        task="bench",
        task_package="bench",
        investigation_package="investigation",
        investigator_image="image",
        models=Models(subjects=(name,)),
        checks=(),
        model_configs_json=canonical({name: configured.model_dump(mode="json", exclude_none=True)}),
        configuration_json="{}",
    )
    authorizer = AuditAuthorizer(policy)
    job = Job(label="native", task="bench", package="bench", models=[name])
    approved = authorizer.authorise(job, "benchmark", set())
    assert approved.model_configs == {name: configured}
    approved.model_configs[name].config.temperature = 0.9
    assert (
        authorizer.authorise(job, "benchmark", set()).model_configs[name].config.temperature == 0.2
    )
    job.model_configs[name] = ModelConfig(model=name, config=GenerateConfig(temperature=0.9))
    with pytest.raises(ToolError, match="configuration must match"):
        authorizer.authorise(job, "benchmark", set())


def test_inspect_pricing_is_used_without_a_router_request(monkeypatch: pytest.MonkeyPatch) -> None:
    import inspect_ai.model

    from inspect_audit import _prices

    monkeypatch.setattr(
        inspect_ai.model,
        "get_model_info",
        lambda name: ModelInfo(
            cost=ModelCost(input=1, output=2, input_cache_read=0, input_cache_write=0)
        ),
    )
    monkeypatch.setattr(
        _prices, "openrouter_prices", lambda names: pytest.fail("unnecessary OpenRouter lookup")
    )
    assert prices_for_models(["openai/native"])["openai/native"]["output"] == 2


def test_unknown_direct_models_do_not_fall_back_to_openrouter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import inspect_ai.model

    from inspect_audit import _prices

    monkeypatch.setattr(inspect_ai.model, "get_model_info", lambda name: None)
    monkeypatch.setattr(_prices, "openrouter_prices", lambda names: pytest.fail("wrong provider"))
    with pytest.raises(ValueError, match="model_prices"):
        prices_for_models(["anthropic/native"])


def test_only_router_models_use_the_router_price_adapter(monkeypatch: pytest.MonkeyPatch) -> None:
    import inspect_ai.model

    from inspect_audit import _prices

    monkeypatch.setattr(inspect_ai.model, "get_model_info", lambda name: None)
    seen = []

    def prices(names):
        seen.extend(names)
        return {name: {"input": 1, "output": 2} for name in names}

    monkeypatch.setattr(_prices, "openrouter_prices", prices)
    name = "openrouter/openai/model"
    assert set(prices_for_models([name, name])) == {name}
    assert seen == [name]


def test_local_inspect_preserves_primary_and_role_configs(tmp_path: Path) -> None:
    task = tmp_path / "bench.py"
    task.write_text(
        'from inspect_ai import Task, task\nfrom inspect_ai.dataset import Sample\nfrom inspect_ai.scorer import match\n@task\ndef bench():\n    return Task(dataset=[Sample(input="q", target="x")], scorer=match())\n'
    )
    runs = LocalRuns(tmp_path / "runs")
    job = Job(
        label="native",
        task=f"{task}@bench",
        package="",
        models=["mockllm/subject"],
        model_roles={"grader": "mockllm/grader"},
        model_configs={
            "mockllm/subject": ModelConfig(
                model="mockllm/subject", config=GenerateConfig(temperature=0.2)
            ),
            "mockllm/grader": ModelConfig(
                model="mockllm/grader", config=GenerateConfig(temperature=0.7)
            ),
        },
    )

    async def run():
        run_id = await runs.submit(job)
        deadline = time.monotonic() + 120
        while (status := await runs.status(run_id)) not in FINISHED:
            assert time.monotonic() < deadline, await runs.runner_log(run_id)
            await asyncio.sleep(0.2)
        assert status == COMPLETE, await runs.runner_log(run_id)
        return await runs.logs(run_id, tmp_path / "unused")

    [path] = asyncio.run(run())
    log = read_eval_log(path, header_only=True)
    assert log.eval.model == "mockllm/subject"
    assert log.eval.model_generate_config.temperature == 0.2
    assert log.eval.model_roles["grader"].config.temperature == 0.7


def test_recorded_judge_settings_are_kept(fixture_log: str, tmp_path: Path) -> None:
    from inspect_ai.log import write_eval_log

    from inspect_audit._logs import index_logs

    log = read_eval_log(fixture_log)
    configured = ModelConfig(
        model="mockllm/grader",
        config=GenerateConfig(temperature=0.3),
        base_url="https://gateway.example",
    )
    log.eval.model_roles = {"grader": configured}
    path = tmp_path / "roles.eval"
    write_eval_log(log, str(path))
    indexed = index_logs([str(path)], task_name="fixture_task", task_args={}, package=None)
    assert indexed.judge_configs == {"grader": configured}


def test_a_recorded_judge_endpoint_is_not_replayed(fixture_log: str, tmp_path: Path) -> None:
    """The logs' run called its judge at its own host's endpoint; this run uses its own.

    A SimpleQA run recorded grader base_url https://openrouter.ai/api/v1; replayed on
    Hawk it bypassed the gateway with the gateway's token and every grade got a 401.
    """
    from inspect_ai.log import write_eval_log
    from test_helpers.logs import fixture_task

    from inspect_audit._task import investigation_task

    log = read_eval_log(fixture_log)
    log.eval.model_roles = {
        "grader": ModelConfig(
            model="mockllm/grader",
            config=GenerateConfig(temperature=0.3),
            base_url="https://elsewhere.example/v1",
        )
    }
    path = tmp_path / "roles.eval"
    write_eval_log(log, str(path))
    task = investigation_task(
        fixture_task("fixture_task"),
        samples=["1"],
        checks=["gold-answer"],
        logs=[str(path)],
        source=str(tmp_path),
    )
    grader = task.model_roles["grader"]
    assert grader.api.base_url != "https://elsewhere.example/v1"
    assert grader.config.temperature == 0.3


def test_investigation_judge_configuration_is_applied_and_cannot_be_supplied_by_the_agent() -> None:
    name = "mockllm/grader"
    configured = ModelConfig(model=name, config=GenerateConfig(temperature=0.4))
    policy = LaunchPolicy(
        task="bench/task",
        task_package="bench-pkg",
        investigation_package="investigation-pkg",
        investigator_image="image",
        models=Models(investigators=("mockllm/investigator",), judges=(name,)),
        checks=("gold-answer",),
        model_configs_json=canonical({name: configured.model_dump(mode="json", exclude_none=True)}),
        configuration_json="{}",
    )
    authorizer = AuditAuthorizer(policy)
    job = Job(
        label="investigation",
        task="inspect_audit/investigation",
        package="investigation-pkg",
        extra_packages=["bench-pkg"],
        models=["mockllm/investigator"],
        task_args={
            "task": "bench/task",
            "investigator_image": "image",
            "source": "bench.task",
            "checks": ["gold-answer"],
            "samples": ["1"],
            "judge": {"grader": name},
            "cost_limit": 0.1,
        },
    )
    approved = authorizer.authorise(job, "investigation", set())
    assert approved.task_args["judge_configs"] == {
        "grader": configured.model_dump(mode="json", exclude_none=True)
    }
    assert "judge_configs" not in job.task_args
    job.task_args["judge_configs"] = {"grader": {"model": name}}
    with pytest.raises(ToolError, match="outside the launch contract"):
        authorizer.authorise(job, "investigation", set())


def test_logs_with_different_judge_settings_are_refused(fixture_log: str, tmp_path: Path) -> None:
    from inspect_ai.log import write_eval_log

    from inspect_audit._logs import LogMismatchError, index_logs

    paths = []
    for i, temperature in enumerate([0.2, 0.8]):
        log = read_eval_log(fixture_log)
        log.eval.eval_id = f"judge-config-{i}"
        log.eval.model_roles = {
            "grader": ModelConfig(
                model="mockllm/grader", config=GenerateConfig(temperature=temperature)
            )
        }
        path = tmp_path / f"roles-{i}.eval"
        write_eval_log(log, str(path))
        paths.append(str(path))
    with pytest.raises(LogMismatchError, match="different judge configurations"):
        index_logs(paths, task_name="fixture_task", task_args={}, package=None)


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -float("inf")])
def test_nonfinite_model_prices_are_rejected(value: float) -> None:
    from inspect_audit._prices import valid_price

    assert not valid_price({"input": value, "output": 1.0})
    assert not valid_price({"input": 1.0, "output": value})
