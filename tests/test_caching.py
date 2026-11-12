"""Anthropic models through OpenRouter cache the conversation (FRICTION 17).

Inspect's OpenRouter provider caches only the system prompt and tools for agent
messages (inspect_ai#5873): the SciCode opus audit's investigators re-bought their whole
history every turn and 31 of 32 hit their cap without a verdict.
"""

from inspect_ai.model import GenerateConfig, ModelConfig

from inspect_audit.dispatch.hawk import hawk_config
from inspect_audit.dispatch.jobs import Job
from inspect_audit.dispatch.launcher import cache_conversations
from inspect_audit.dispatch.policy import (
    AuditAuthorizer,
    LaunchPolicy,
    Models,
    with_caching,
)

OPUS = "openrouter/anthropic/claude-opus-5.5"
WORKAROUND = {"cache_prompt": False, "extra_body": {"cache_control": {"type": "ephemeral"}}}


def test_only_anthropic_through_openrouter_is_changed() -> None:
    assert with_caching("openai/gpt-6-luna", None) is None
    config = with_caching(OPUS, None)
    assert config is not None
    assert config.config.model_dump(exclude_none=True) == WORKAROUND


def test_an_operators_own_caching_choice_stands() -> None:
    chosen = ModelConfig(model=OPUS, config=GenerateConfig(cache_prompt=True))
    assert with_caching(OPUS, chosen) is chosen


def test_child_jobs_on_opus_send_the_workaround_to_hawk() -> None:
    policy = LaunchPolicy(
        task="b/t",
        task_package="b",
        investigation_package="ia",
        investigator_image="i",
        models=Models(subjects=(OPUS, "openai/gpt-6-luna")),
        checks=("gold-answer",),
        configuration_json="{}",
    )
    job = Job(label="run", task="b/t", package="b", models=[OPUS, "openai/gpt-6-luna"])
    approved = AuditAuthorizer(policy).authorise(job, "benchmark", set())
    config = hawk_config(approved)
    items = {g["items"][0]["name"]: g["items"][0] for g in config["models"]}
    assert items[OPUS]["args"]["config"] == WORKAROUND
    assert "args" not in items["openai/gpt-6-luna"]


def test_the_auditors_own_opus_config_gets_it_at_launch() -> None:
    submitted = cache_conversations(
        {"models": [{"package": "inspect-ai", "items": [{"name": OPUS}, {"name": "openai/x"}]}]}
    )
    [opus, other] = submitted["models"][0]["items"]
    assert opus["args"]["config"] == WORKAROUND
    assert "args" not in other
