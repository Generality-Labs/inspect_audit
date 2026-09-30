"""The declared per-eval configuration: what to scan and which logs count."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from inspect_audit.findings.config import (
    DEFAULT_CONFIG_PATH,
    Config,
    DatasetConfig,
    EvalConfig,
    LogFilter,
    load_config,
)


def test_load_config_reads_evals_and_defaults(tmp_path: Path) -> None:
    path = tmp_path / "pilot.yaml"
    path.write_text(
        "defaults:\n  logs:\n    include_mock: false\n"
        "evals:\n  inspect_evals/stereoset:\n    dataset:\n      path: McGill-NLP/stereoset\n"
        "      config: intersentence\n      split: validation\n"
        "      fields: {question: context, answer: sentences, id: id}\n"
        "    logs:\n      task_args: {}\n"
    )
    config = load_config(path)
    stereoset = config.for_eval("inspect_evals/stereoset")
    assert stereoset.dataset == DatasetConfig(
        path="McGill-NLP/stereoset",
        config="intersentence",
        split="validation",
        fields={"question": "context", "answer": "sentences", "id": "id"},
    )
    assert stereoset.logs.task_args == {}
    assert stereoset.logs.include_mock is False


def test_for_eval_falls_back_to_defaults() -> None:
    config = Config(defaults=EvalConfig(logs=LogFilter(include_mock=True)))
    other = config.for_eval("inspect_evals/hle")
    assert other.dataset is None
    assert other.logs.include_mock is True


def test_unknown_keys_are_rejected(tmp_path: Path) -> None:
    path = tmp_path / "pilot.yaml"
    path.write_text("evals:\n  inspect_evals/x:\n    dataset:\n      pth: a/b\n")
    with pytest.raises(ValidationError):
        load_config(path)


def test_non_mapping_file_is_a_value_error(tmp_path: Path) -> None:
    path = tmp_path / "pilot.yaml"
    path.write_text("- just\n- a list\n")
    with pytest.raises(ValueError, match="mapping"):
        load_config(path)


def test_packaged_default_declares_stereoset() -> None:
    config = load_config(DEFAULT_CONFIG_PATH)
    stereoset = config.for_eval("inspect_evals/stereoset")
    assert stereoset.dataset is not None
    assert stereoset.dataset.config == "intersentence"
    assert stereoset.dataset.fields["question"] == "context"
    assert config.defaults.logs.include_mock is False


def test_eval_entry_inherits_defaults_it_does_not_set() -> None:
    config = Config(
        defaults=EvalConfig(logs=LogFilter(include_mock=True)),
        evals={"inspect_evals/stereoset": EvalConfig(dataset=DatasetConfig(split="validation"))},
    )
    stereoset = config.for_eval("inspect_evals/stereoset")
    assert stereoset.dataset == DatasetConfig(split="validation")
    assert stereoset.logs.include_mock is True  # inherited: the entry said nothing about logs
    explicit = Config(
        defaults=EvalConfig(logs=LogFilter(include_mock=True)),
        evals={"inspect_evals/x": EvalConfig(logs=LogFilter(include_mock=False))},
    )
    assert explicit.for_eval("inspect_evals/x").logs.include_mock is False


def test_unknown_field_role_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValidationError):
        DatasetConfig(fields={"questoin": "ctx"})  # type: ignore[dict-item]
    path = tmp_path / "pilot.yaml"
    path.write_text("evals:\n  inspect_evals/x:\n    dataset:\n      fields: {questoin: ctx}\n")
    with pytest.raises(ValidationError):
        load_config(path)
