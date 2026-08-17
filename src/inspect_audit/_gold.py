"""Where grading lives, for the auditor to go and read."""

from pathlib import Path

from inspect_ai import Task
from inspect_ai.dataset import Sample
from inspect_ai.util import resource

__all__ = ["grading_doc"]

_TEMPLATE = Path(__file__).parent / "templates" / "grading.md"


def scorer_sources(task: Task) -> list[tuple[str, str]]:
    """Each of a task's scorers as `(qualified name, module)`.

    A scorer routinely delegates outside the task's own package, so the module is
    what an auditor needs in order to find the code.

    Args:
        task: The task being audited.

    Returns:
        One entry per scorer, in the order the task declares them.
    """
    scorers = task.scorer if isinstance(task.scorer, list) else [task.scorer]
    return [
        (
            getattr(fn, "__qualname__", getattr(fn, "__name__", "?")),
            getattr(fn, "__module__", "?"),
        )
        for scorer in scorers
        if scorer is not None
        for fn in [getattr(scorer, "__wrapped__", scorer)]
    ]


def grading_doc(task: Task, sample: Sample) -> str:
    """Render `gold/grading.md` for one item.

    Args:
        task: The task being audited.
        sample: The sample being audited.

    Returns:
        Markdown naming the task's scorers and pointing at the case's own logs.
    """
    named = scorer_sources(task)
    return resource(str(_TEMPLATE), type="file").format(
        scorers="\n".join(f"- `{name}`, defined in `{module}`" for name, module in named)
        or "- not recovered",
        modules=" ".join(sorted({module for _, module in named})) or "?",
        metadata_keys=(
            ", ".join(f"`{k}`" for k in sorted((sample.metadata or {}).keys())) or "(none)"
        ),
    )
