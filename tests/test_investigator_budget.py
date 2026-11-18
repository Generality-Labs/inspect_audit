"""An investigator's own budget: past it, it is asked for best estimates, not killed."""

from inspect_ai import Task, eval
from inspect_ai.agent import AgentSubmit, react
from inspect_ai.dataset import Sample
from inspect_ai.model import ChatMessageUser, ModelOutput, ModelUsage, get_model
from inspect_ai.solver import Generate, Solver, TaskState, solver
from inspect_ai.tool import Tool, tool
from inspect_ai.util import store_as

from inspect_audit._investigator import BUDGET_REACHED, within_budget
from inspect_audit._prices import register_prices
from inspect_audit._verdicts import BudgetCap

MODEL = "mockllm/budget"


@tool
def probe() -> Tool:
    async def execute() -> str:
        """Look at something."""
        return "nothing new"

    return execute


def _call(name: str, tokens: int = 1_000_000, **args: str) -> ModelOutput:
    out = ModelOutput.for_tool_call(MODEL, name, args)
    # a million input tokens is one dollar at the price registered below
    out.usage = ModelUsage(input_tokens=tokens, output_tokens=0, total_tokens=tokens)
    return out


def _run(outputs: list[ModelOutput], budget: float) -> TaskState:
    register_prices({MODEL: {"input": 1.0, "output": 1.0}})
    model = get_model(MODEL, custom_outputs=outputs)

    @solver
    def budgeted() -> Solver:
        async def solve(state: TaskState, generate: Generate) -> TaskState:
            agent = react(name="investigator", tools=[probe()], model=model)
            wrap_up = react(
                name="investigator",
                prompt=None,
                tools=[],
                model=model,
                submit=AgentSubmit(name="submit"),
            )
            result = await within_budget(agent, wrap_up, [ChatMessageUser(content="go")], budget)
            state.messages = result.messages
            state.output = result.output
            state.metadata["capped"] = store_as(BudgetCap).capped
            return state

        return solve

    log = eval(Task(dataset=[Sample(input="go")], solver=budgeted()), model=model)[0]
    assert log.status == "success", log.error
    assert log.samples
    return log.samples[0]  # type: ignore[return-value]


def test_budget_reached_asks_for_best_estimates() -> None:
    # three probes cost $3 against a $2.50 budget; then the wrap-up submits within its grace
    best = _call("submit", tokens=100_000, answer="best estimate")
    sample = _run(
        [_call("probe"), _call("probe"), _call("probe"), best],
        budget=2.5,
    )
    assert sample.metadata["capped"] is True
    texts = [m.text for m in sample.messages if m.role == "user"]
    assert BUDGET_REACHED.format(budget=2.5) in texts
    assert sample.output.completion.endswith("best estimate")


def test_within_budget_runs_as_usual() -> None:
    sample = _run([_call("probe"), _call("submit", answer="done")], budget=10.0)
    assert sample.metadata["capped"] is False
    assert sample.output.completion.endswith("done")
