"""Real tasks and real logs for tests.

Produced by running actual evals under `mockllm` rather than checked in, so a fixture
cannot drift from what Inspect writes today.
"""

from inspect_ai import Task, eval
from inspect_ai.dataset import MemoryDataset, Sample
from inspect_ai.scorer import match

__all__ = ["fixture_task", "run_fixture_eval"]


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
