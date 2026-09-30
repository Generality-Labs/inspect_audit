"""What each eval's producers should examine, declared rather than inferred.

Auto-detection of dataset configuration and log relevance failed on five of six evals in the
acceptance sweep. The pilot declares these per eval; an eval without an entry gets `defaults`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator

DEFAULT_CONFIG_PATH = Path(__file__).parent / "pilot.yaml"

FieldRole = Literal["question", "answer", "id"]


class DatasetConfig(BaseModel):
    """Which dataset to scan.

    With none of `path`, `config`, `split`, `revision` or `fields` set, the scan goes through the
    eval's task: `task`, or the first task in eval.yaml. Setting any of them scans the HuggingFace
    dataset directly instead, with `path` falling back to eval.yaml's HuggingFace asset.
    """

    model_config = ConfigDict(extra="forbid")
    task: str | None = None
    path: str | None = None
    config: str | None = None
    split: str | None = None
    revision: str | None = None
    # inspect-dataset column names by role; a misspelled role is a validation error, not a no-op
    fields: dict[FieldRole, str] = Field(default_factory=dict)

    @property
    def selects_hf(self) -> bool:
        """Whether this declaration asks for a direct HuggingFace scan rather than a task scan."""
        return bool(self.path or self.config or self.split or self.revision or self.fields)

    @model_validator(mode="after")
    def _task_or_hf(self) -> DatasetConfig:
        if self.task is not None and (not self.task or "/" in self.task):
            raise ValueError(
                f"task must be a bare task name from the eval's eval.yaml, such as arc_challenge, "
                f"not {self.task!r}"
            )
        if self.task and self.selects_hf:
            raise ValueError(
                "task cannot be combined with path, config, split, revision or fields: "
                "a task scan uses the eval's own loader"
            )
        return self


class LogFilter(BaseModel):
    """Which of the matched logs count as attempts of this eval.

    `task_args` None means: logs with any arguments are used, but only those with no
    arguments are compared against eval.yaml's declared sample count. A dict means only
    logs whose arguments equal it are used at all.
    """

    model_config = ConfigDict(extra="forbid")
    task_args: dict[str, JsonValue] | None = None
    include_mock: bool = False


class EvalConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    dataset: DatasetConfig | None = None
    logs: LogFilter = Field(default_factory=LogFilter)


class Config(BaseModel):
    model_config = ConfigDict(extra="forbid")
    defaults: EvalConfig = Field(default_factory=EvalConfig)
    evals: dict[str, EvalConfig] = Field(default_factory=dict)

    def for_eval(self, target: str) -> EvalConfig:
        """The eval's entry with every field it did not set taken from `defaults`."""
        entry = self.evals.get(target)
        if entry is None:
            return self.defaults
        overrides = {name: getattr(entry, name) for name in entry.model_fields_set}
        return self.defaults.model_copy(update=overrides)


def load_config(path: Path) -> Config:
    loaded = yaml.safe_load(path.read_text())
    if not isinstance(loaded, dict):
        raise ValueError(f"{path} must be a mapping with `defaults` and `evals`")
    return Config.model_validate(loaded)
