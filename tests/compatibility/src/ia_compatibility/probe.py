"""Deterministic auditor tool exercise. No external model requests are made.

The real auditor stages each sample and exposes its real tools. A scripted model
drives those tools, probes the grader, records an unresolved validity judgment and
submits. Judge calls use explicit mock fixtures when declared; otherwise they are refused. Tool events
in the native Inspect log are the compatibility evidence, not the validity label.
"""

import importlib
import json
from contextlib import suppress
from typing import Any

from inspect_ai import Task, task, task_with
from inspect_ai.model import (
    ChatMessage,
    GenerateConfig,
    ModelAPI,
    ModelCost,
    ModelInfo,
    ModelOutput,
    get_model,
    modelapi,
    set_model_info,
)
from inspect_ai.scorer import CORRECT, INCORRECT, NOANSWER, Score, Target, accuracy, scorer
from inspect_ai.solver import TaskState
from inspect_ai.tool import ToolChoice, ToolInfo
from inspect_ai.util import registry_create

from inspect_audit._openrouter import openrouter_prices
from inspect_audit._prices import register_prices
from inspect_audit._registry import log_files, resolve_source
from inspect_audit._task import audit_task, numbered

from .judges import MOCK_JUDGE, mock_judge
from .probe_results import outcomes


@scorer(metrics=[accuracy()])
def compatibility(expected: dict[str, bool]):
    async def score(state: TaskState, target: Target) -> Score:
        result = outcomes(state.messages, expected)
        return Score(
            value={"passed": CORRECT, "partial": NOANSWER, "failed": INCORRECT}[result["status"]],
            explanation=json.dumps(result),
            metadata=result,
        )

    return score


@modelapi(name="ia_probe")
class ProbeAPI(ModelAPI):
    async def generate(
        self,
        input: list[ChatMessage],
        tools: list[ToolInfo],
        tool_choice: ToolChoice,
        config: GenerateConfig,
    ) -> ModelOutput:
        available = {t.name for t in tools}
        if self.model_name != "driver" or "record_verdict" not in available:
            raise RuntimeError("API_FREE_PROBE: model-based grading is disabled")
        results = [m for m in input if m.role == "tool"]
        done = {m.function for m in results}

        def call(name: str, **args: Any) -> ModelOutput:
            return ModelOutput.for_tool_call("ia_probe/driver", name, args)

        if "auditor_bash" not in done:
            return call(
                "auditor_bash",
                command='python3 -c \'import json; d=json.load(open("/audit/item.json")); print(json.dumps({k:d[k] for k in ["sample_id","target","choices","files_in_their_box","has_setup_script"]})); assert d.get("benchmark_source"), "no source manifest"\'',
            )
        if "attempts" not in done:
            return call("attempts")
        attempts: list[dict[str, Any]] = []
        listed = next(m for m in results if m.function == "attempts")
        with suppress(json.JSONDecodeError):
            attempts = json.loads(listed.text)
        if attempts and "read_attempt" not in done:
            return call(
                "read_attempt",
                eval_id=attempts[0]["eval_id"],
                epoch=attempts[0]["epoch"],
                start=0,
                count=2,
            )
        if "benchmark_bash" in available and "benchmark_bash" not in done:
            return call("benchmark_bash", command="pwd\nls -al")
        if "grade" in available and "grade" not in done:
            staged = next(m for m in results if m.function == "auditor_bash")
            item = json.loads(staged.text)
            if attempts:
                return call(
                    "grade", answer="", eval_id=attempts[0]["eval_id"], epoch=attempts[0]["epoch"]
                )
            return call("grade", answer=next(iter(item["target"]), ""), eval_id="", epoch=0)
        evidence = [
            {
                "source": f"tool:{m.function}:{m.tool_call_id}",
                "observed": (m.error.message if m.error else m.text)[:1500]
                or "Tool returned no text.",
            }
            for m in results
            if m.function not in ("record_verdict", "submit")
        ]
        if "record_verdict" not in done:
            return call(
                "record_verdict",
                item="gold-answer",
                grade="UNVERIFIABLE",
                explanation="Scripted functionality probe only; no scientific validity assessment was attempted. Tool outcomes are preserved as evidence.",
                evidence=evidence,
                approaches="Deterministic tool calls, no LLM inference.",
                tried="Stage sample/source, enumerate attempts, read a transcript when present, inspect the benchmark box and invoke its grader.",
                remarks="A rejected candidate is a grader result, not a failed integration or a benchmark defect. Judge calls use explicit mock fixtures when declared; otherwise they are disabled.",
                details='{"independent": false}',
            )
        return call(
            "submit",
            environment_issues=[
                e
                for e, m in zip(
                    evidence,
                    [m for m in results if m.function not in ("record_verdict", "submit")],
                    strict=True,
                )
                if m.error
            ],
            unresolved=[{"source": "API-free probe", "observed": "Validity was not assessed."}],
            improvements=[],
        )


@task
def probe(spec: dict[str, Any], limit: int = 1) -> Task:
    """Exercise the production audit task with a scripted, API-free tool driver."""
    if limit < 1:
        raise ValueError("probe limit must be positive")
    # Hawk replaces Task.model with its declared grid model after construction.
    # Price that real declaration, but bind the actual agent to our driver below.
    # This fetches public price metadata; it never requests a model completion.
    declared_model = str(get_model())
    if declared_model.startswith("openrouter/"):
        register_prices(openrouter_prices([declared_model]))
    for name in ("driver", "judge"):
        set_model_info(
            f"ia_probe/{name}",
            ModelInfo(cost=ModelCost(input=0, output=0, input_cache_write=0, input_cache_read=0)),
        )
    fixtures = spec.get("mock_judges", {})
    if not isinstance(fixtures, dict):
        raise ValueError("mock_judges must map model role names to response fixtures")
    mocked = {role: mock_judge(response) for role, response in fixtures.items()}
    if mocked:
        set_model_info(
            MOCK_JUDGE,
            ModelInfo(cost=ModelCost(input=0, output=0, input_cache_write=0, input_cache_read=0)),
        )
    importlib.import_module(spec["source"])
    args = dict(spec.get("task_args", {}))
    their = registry_create("task", spec["task"], **args)
    samples = spec.get("samples")
    if not samples:
        dataset, _ = numbered(their)
        samples = [str(s.id) for s in dataset[:limit]]
    logs = spec.get("logs") or []
    judges = {
        role: MOCK_JUDGE if role in mocked else "ia_probe/judge"
        for role in set(spec.get("judges", {})) | set(mocked)
    }
    audit = audit_task(
        spec["task"],
        task_args=args,
        samples=samples[:limit],
        checks=["gold-answer"],
        source=resolve_source(spec["source"]),
        logs=log_files(logs) if logs else None,
        judge=judges,
        auditor_image=spec["auditor_image"],
        cost_limit=0.01,
        time_limit=900,
        message_limit=20,
        auditor_model=get_model("ia_probe/driver"),
    )
    expected = {
        "logs": bool(logs),
        "benchmark_box": bool(their.sandbox or any(s.sandbox for s in their.dataset)),
        "grader": bool(their.scorer),
        "mock_judge": bool(mocked),
    }
    validity_scorers = (
        list(audit.scorer)
        if isinstance(audit.scorer, list)
        else [audit.scorer]
        if audit.scorer
        else []
    )
    return task_with(
        audit,
        model=get_model("ia_probe/driver"),
        model_roles={**(audit.model_roles or {}), **mocked},
        # Inspect truncates tool output for model context. This scripted driver
        # needs complete grader JSON, including SciCode's long explanations.
        config=GenerateConfig(max_tool_output=1_000_000),
        name=f"probe/{spec['task']}",
        scorer=[*validity_scorers, compatibility(expected)],
        metadata={**(audit.metadata or {}), "functionality_probe": expected},
    )
