"""Which external producers run, and how they are invoked."""

from __future__ import annotations

import os
import shlex
from collections.abc import Mapping
from dataclasses import dataclass

LINT_SPEC = "inspect-evals-lint==0.10.0"
DATASET_VERSION = "0.5.0"
DATASET_SPEC = f"inspect-dataset=={DATASET_VERSION}"
# task scans load a replay task, which needs inspect-dataset's inspect_ai extra
DATASET_TASK_SPEC = f"inspect-dataset[inspect]=={DATASET_VERSION}"

LINT_ENV = "INSPECT_AUDIT_LINT_CMD"
DATASET_ENV = "INSPECT_AUDIT_DATASET_CMD"
DATASET_TASK_ENV = "INSPECT_AUDIT_DATASET_TASK_CMD"
DATASET_DUMP_ENV = "INSPECT_AUDIT_DATASET_DUMP_CMD"
EVAL_ENV_ENV = "INSPECT_AUDIT_EVAL_ENV"
HAWK_ENV = "INSPECT_AUDIT_HAWK_CMD"


@dataclass(frozen=True)
class ProducerConfig:
    """Command prefixes for the external producers. Each runs in its own `uvx` environment by default.

    A task scan has two steps. `dataset_dump` runs the dump script with the Inspect Evals
    checkout's locked dependencies plus the eval's own extra and group (`{eval_deps}`), in a
    scratch environment (`eval_env`, by default a cache directory per checkout) so the checkout's
    `.venv` is never touched. `{ie_root}` is replaced by the checkout path. That imports the eval's
    code on the host, as running the eval would. `dataset_task` then scans the dumped samples in
    inspect-dataset's own environment, with the checkout's locked inspect-ai (`{inspect_ai}`).
    """

    lint: tuple[str, ...] = ("uvx", "--from", LINT_SPEC, "inspect-evals-lint")
    dataset: tuple[str, ...] = ("uvx", "--from", DATASET_SPEC, "inspect-dataset")
    # --frozen: use the checkout's uv.lock as it is, never rewrite it
    dataset_dump: tuple[str, ...] = (
        "uv",
        "run",
        "--project",
        "{ie_root}",
        "--frozen",
        "{eval_deps}",
        "python",
    )
    # {inspect_ai}: `--with inspect-ai==<the checkout's locked version>`, so the replay reads the
    # samples with the Sample model the eval wrote them with
    dataset_task: tuple[str, ...] = (
        "uvx",
        "--from",
        DATASET_TASK_SPEC,
        "{inspect_ai}",
        "inspect-dataset",
    )
    eval_env: str | None = None
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
            dataset_dump=tuple(shlex.split(source[DATASET_DUMP_ENV]))
            if source.get(DATASET_DUMP_ENV)
            else defaults.dataset_dump,
            dataset_task=tuple(shlex.split(source[DATASET_TASK_ENV]))
            if source.get(DATASET_TASK_ENV)
            else defaults.dataset_task,
            eval_env=source.get(EVAL_ENV_ENV) or defaults.eval_env,
            hawk=tuple(shlex.split(source[HAWK_ENV])) if source.get(HAWK_ENV) else defaults.hawk,
        )
