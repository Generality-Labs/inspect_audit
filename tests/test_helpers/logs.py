"""Real tasks and real logs for tests.

Produced by running actual evals under `mockllm` rather than checked in, so a fixture
cannot drift from what Inspect writes today.
"""

from inspect_ai import Task, eval
from inspect_ai.dataset import MemoryDataset, Sample
from inspect_ai.scorer import match

__all__ = ["fixture_task", "run_fixture_eval", "run_graded_eval", "write_fixture_log"]


def fixture_task(name: str = "fixture_task") -> Task:
    """A three-sample task with a real scorer."""
    return Task(
        name=name,
        dataset=MemoryDataset([Sample(id=i, input=f"q{i}", target="ANSWER") for i in (1, 2, 3)]),
        scorer=match(),
    )


def run_fixture_eval(log_dir: str, *, name: str = "fixture_task", epochs: int = 1) -> str:
    """Run a real eval and return its log location."""
    return eval(
        fixture_task(name),
        model="mockllm/model",
        log_dir=log_dir,
        display="none",
        epochs=epochs,
    )[0].location


def run_graded_eval(log_dir: str) -> str:
    """A real eval whose recorded grades are MIXED (C, I, C).

    A fixture where every grade is identical cannot tell a faithful regrade
    from a broken one that agrees with anything; this one can.
    """
    from inspect_ai.model import ModelOutput, get_model

    model = get_model(
        "mockllm/model",
        custom_outputs=[
            ModelOutput.from_content("mockllm/model", content)
            for content in ("ANSWER", "WRONG", "ANSWER")
        ],
    )
    return eval(
        fixture_task("graded_task"),
        model=model,
        log_dir=log_dir,
        display="none",
        max_samples=1,  # sequential, so outputs map to samples 1, 2, 3
    )[0].location


def write_fixture_log(location: str, *, padding: dict[str, int] | None = None) -> str:
    """A finished log written by Inspect's own writer, without running an eval.

    Samples a, b and c, two epochs each, scored. A run counts tokens with a tokenizer it
    downloads; this needs nothing from the network. `padding` gives a sample id an
    incompressible answer of that many bytes, to tell a ranged read from a download.
    """
    import os

    from inspect_ai.log import (
        EvalConfig,
        EvalDataset,
        EvalLog,
        EvalSample,
        EvalSpec,
        write_eval_log,
    )
    from inspect_ai.model import ChatMessageAssistant, ChatMessageUser, ModelOutput
    from inspect_ai.scorer import Score

    def answer(i: str) -> str:
        size = (padding or {}).get(i, 0)
        return f"{i} " * 5000 + (os.urandom(size // 2).hex() if size else "")

    write_eval_log(
        EvalLog(
            eval=EvalSpec(
                created="2026-01-01T00:00:00+00:00",
                task="fixture",
                dataset=EvalDataset(),
                model="mockllm/model",
                config=EvalConfig(),
            ),
            status="success",
            samples=[
                EvalSample(
                    id=i,
                    epoch=e,
                    input=f"say {i}",
                    target=i,
                    messages=[
                        ChatMessageUser(content=f"say {i}"),
                        ChatMessageAssistant(content=answer(i)),
                    ],
                    output=ModelOutput.from_content("mockllm/model", i),
                    scores={"match": Score(value="C" if e == 1 else "I")},
                )
                for i in ("a", "b", "c")
                for e in (1, 2)
            ],
        ),
        location,
    )
    return location
