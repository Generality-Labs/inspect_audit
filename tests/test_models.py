"""Inspect model identities, settings, pricing and launch permissions round-trip."""

import asyncio
import time
from pathlib import Path

import pytest
from inspect_ai.log import read_eval_log
from inspect_ai.model import GenerateConfig, ModelConfig
from inspect_ai.tool import ToolError
from inspect_ai.util import Store

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
    assert "inspect-ai==0.3.285" in config.packages
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


@pytest.mark.parametrize(
    "price",
    [
        {"input": float("nan"), "output": 1.0},
        {"input": 1.0, "output": float("inf")},
        {"input": -1.0, "output": 1.0},  # routers' "depends on the route"
        {"input": 0.0, "output": 0.0},  # would read every call as free
    ],
)
def test_unusable_prices_are_refused(price: dict[str, float]) -> None:
    from inspect_ai.model import get_model_info

    from inspect_audit._prices import UnpricedModelError, register_price

    with pytest.raises(UnpricedModelError):
        register_price("ia-test/unusable", price)
    assert get_model_info("ia-test/unusable") is None


def test_prices_come_from_hawks_table_under_the_jobs_model_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One table: Hawk's /usage/prices, keyed by Hawk's public name, registered under the
    name the job calls (FRICTION 84: dated snapshots are priced only there)."""
    from inspect_ai.model import get_model_info

    from inspect_audit import _prices

    rows = {
        "openrouter/deepseek/snap-20261017": {
            "model": "openrouter/deepseek/snap-20261017",
            "input_cost_per_1m": 0.05,
            "output_cost_per_1m": 0.15,
            "cache_read_input_cost_per_1m": 0.004,
            "cache_write_input_cost_per_1m": None,
        },
        "claude-x": {"model": "claude-x", "input_cost_per_1m": 5, "output_cost_per_1m": 25},
    }
    monkeypatch.setenv("HAWK_API_URL", "https://hawk.test")
    monkeypatch.setattr(_prices, "_hawk_table", lambda url: rows)
    _prices.price_models(["openai/openrouter/deepseek/snap-20261017", "anthropic/claude-x"])
    cost = get_model_info("openai/openrouter/deepseek/snap-20261017").cost  # type: ignore[union-attr]
    assert (cost.input, cost.output, cost.input_cache_read, cost.input_cache_write) == (
        0.05,
        0.15,
        0.004,
        0.0,
    )
    assert get_model_info("anthropic/claude-x").cost.output == 25  # type: ignore[union-attr]
    # a model Hawk does not price is refused, naming every such model; mock models are free
    with pytest.raises(_prices.UnpricedModelError, match="openai/unlisted-xyz, google/nope"):
        _prices.prices_for(["anthropic/claude-x", "openai/unlisted-xyz", "google/nope"])
    assert _prices.prices_for(["mockllm/model"]) == {}
    # a listed row with an unusable price is refused too
    rows["claude-free"] = {"model": "claude-free", "input_cost_per_1m": 0, "output_cost_per_1m": 0}
    with pytest.raises(_prices.UnpricedModelError, match="claude-free"):
        _prices.prices_for(["anthropic/claude-free"])


def test_off_hawk_nothing_is_fetched(monkeypatch: pytest.MonkeyPatch) -> None:
    from inspect_audit import _prices

    monkeypatch.delenv("HAWK_API_URL", raising=False)
    monkeypatch.setattr(_prices, "_hawk_table", lambda url: pytest.fail("fetched off Hawk"))
    _prices.price_models(["openai/anything"])


def test_child_jobs_register_the_prices_they_are_given_and_never_fetch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Investigation and regrade jobs run with restricted credentials on Hawk, which
    cannot read Hawk's price list: they only register what the audit handed them."""
    from inspect_ai import Task
    from inspect_ai.dataset import Sample
    from inspect_ai.model import get_model_info

    from inspect_audit import _prices, _registry, _regrade

    monkeypatch.setenv("HAWK_API_URL", "https://hawk.test")
    monkeypatch.setattr(_prices, "_hawk_table", lambda url: pytest.fail("a child fetched prices"))
    stub = lambda *a, **k: Task(dataset=[Sample(input="q")])  # noqa: E731
    monkeypatch.setattr(_registry, "investigation_task", stub)
    monkeypatch.setattr(_regrade, "regrade_task", stub)
    monkeypatch.setattr(_registry, "log_files", lambda logs: logs)
    _registry.investigation(
        task="b/t",
        samples=["1"],
        checks=["gold-answer"],
        cost_limit=0.2,
        judge={"grader": "ia-test/judge"},
        prices={"ia-test/investigator": {"input": 1.0, "output": 2.0}},
    )
    _registry.regrade(
        task="b/t",
        logs=["x.eval"],
        judge={"grader": "ia-test/judge"},
        cost_limit=0.2,
        prices={"ia-test/judge": {"input": 3.0, "output": 4.0}},
    )
    assert get_model_info("ia-test/investigator").cost.output == 2  # type: ignore[union-attr]
    assert get_model_info("ia-test/judge").cost.output == 4  # type: ignore[union-attr]
