"""Which external producers run, and how they are invoked."""

from __future__ import annotations

import os
import shlex
from collections.abc import Mapping
from dataclasses import dataclass

LINT_SPEC = "inspect-evals-lint==0.9.0"
DATASET_SPEC = "git+https://github.com/Generality-Labs/inspect_dataset@afbc94c0b509"

LINT_ENV = "INSPECT_AUDIT_LINT_CMD"
DATASET_ENV = "INSPECT_AUDIT_DATASET_CMD"
DATASET_TASK_ENV = "INSPECT_AUDIT_DATASET_TASK_CMD"
HAWK_ENV = "INSPECT_AUDIT_HAWK_CMD"


@dataclass(frozen=True)
class ProducerConfig:
    """Command prefixes for the external producers. Each runs in its own `uvx` environment by default.

    `dataset_task` is the exception: scanning an eval's task imports the eval, so it runs in the
    Inspect Evals checkout's own environment with inspect-dataset added. `{ie_root}` in it is
    replaced by the checkout path. That imports the eval's code on the host, as running the eval
    would.
    """

    lint: tuple[str, ...] = ("uvx", "--from", LINT_SPEC, "inspect-evals-lint")
    dataset: tuple[str, ...] = ("uvx", "--from", DATASET_SPEC, "inspect-dataset")
    # --frozen: use the checkout's uv.lock as it is, never rewrite it
    dataset_task: tuple[str, ...] = (
        "uv",
        "run",
        "--project",
        "{ie_root}",
        "--frozen",
        "--with",
        DATASET_SPEC,
        "inspect-dataset",
    )
    hawk: tuple[str, ...] = ("hawk",)
    timeout_s: float = 1800.0

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> ProducerConfig:
        """Defaults, with the `INSPECT_AUDIT_*_CMD` variables shell-split over them."""
        source = os.environ if env is None else env
        defaults = cls()
        return cls(
            lint=tuple(shlex.split(source[LINT_ENV])) if source.get(LINT_ENV) else defaults.lint,
            dataset=tuple(shlex.split(source[DATASET_ENV]))
            if source.get(DATASET_ENV)
            else defaults.dataset,
            dataset_task=tuple(shlex.split(source[DATASET_TASK_ENV]))
            if source.get(DATASET_TASK_ENV)
            else defaults.dataset_task,
            hawk=tuple(shlex.split(source[HAWK_ENV])) if source.get(HAWK_ENV) else defaults.hawk,
        )
