"""Which external producers run, and how they are invoked."""

from __future__ import annotations

import os
import shlex
from collections.abc import Mapping
from dataclasses import dataclass

LINT_SPEC = "inspect-evals-lint==0.7.0"
DATASET_SPEC = "git+https://github.com/Generality-Labs/inspect_dataset@afbc94c0b509"

LINT_ENV = "INSPECT_AUDIT_LINT_CMD"
DATASET_ENV = "INSPECT_AUDIT_DATASET_CMD"


@dataclass(frozen=True)
class ProducerConfig:
    """Command prefixes for the external producers. Each runs in its own `uvx` environment by default."""

    lint: tuple[str, ...] = ("uvx", "--from", LINT_SPEC, "inspect-evals-lint")
    dataset: tuple[str, ...] = ("uvx", "--from", DATASET_SPEC, "inspect-dataset")
    timeout_s: float = 1800.0

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> ProducerConfig:
        """Defaults, with `INSPECT_AUDIT_LINT_CMD` and `INSPECT_AUDIT_DATASET_CMD` shell-split over them."""
        source = os.environ if env is None else env
        defaults = cls()
        return cls(
            lint=tuple(shlex.split(source[LINT_ENV])) if source.get(LINT_ENV) else defaults.lint,
            dataset=tuple(shlex.split(source[DATASET_ENV])) if source.get(DATASET_ENV) else defaults.dataset,
        )
