# Input Selection Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The deterministic producers examine only the inputs they should, say exactly what they examined and what they left out, and the summaries show that beside the findings.

**Architecture:** A declared per-eval configuration file (`pilot.yaml`) replaces the hard-coded dataset override table and adds a log filter per eval. The dataset adapter derives its scan arguments from the declaration first and `eval.yaml` second, and records what it examined in `Run.inputs`. The header adapter matches qualified registry names exactly, excludes mock-model runs and non-default task arguments with recorded reasons, and records used and excluded logs in `Run.inputs`. The renderer gains an Inputs section per eval and input counts in the sweep table.

**Tech Stack:** Python 3.13, pydantic v2, PyYAML, inspect_ai log reading, pytest. Gate: `uv run pre-commit run --all-files`, `uv run basedpyright src`, `uv run pytest tests/findings`.

**Spec:** `docs/roadmap.md` milestone 1 ("Input selection"), `docs/findings-prototype-review.md` section 1, and `docs/superpowers/specs/2026-09-25-findings-prototype-design.md` (Dataset, Header and CLI sections, updated in Task 7).

## Global Constraints

- Python `>=3.11` in `pyproject.toml`; develop on 3.13 (`uv sync --python 3.13`) so the hawk extra installs.
- Ruff rule set and formatter from `pyproject.toml`; basedpyright strict on `src`. No new ignores.
- Every model uses `ConfigDict(extra="forbid")`, matching the existing `findings/models.py`.
- Adapters never raise out of `run()`: a producer that cannot run returns a skip run (`adapters.skip_run`).
- Run files are immutable and the CLI writes `<slug>/runs/<run id>.run.json` plus `<slug>/current.json`; this plan does not change that layout.
- Nothing under `agent_artefacts/` or `artefacts/` is committed.
- Commit messages end with `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`.

## Review Focus

1. A log whose registry name is `audit/inspect_evals/scicode` must not be treated as a run of `inspect_evals/scicode`. Test in Task 3.
2. An eval with no entry in `pilot.yaml` and no HuggingFace asset in `eval.yaml` must produce a skip that names both places it looked. Test in Task 2.
3. When every matched log is excluded, the header run must be a skip that says how many were excluded and why, not an empty pass. Test in Task 4.
4. A log with non-default task arguments must not fire `header.dataset_samples` against the default `dataset_samples`, but must still take part in version drift. Test in Task 4.
5. A `pilot.yaml` with a misspelled key must fail loudly at load time, not silently ignore the entry. Test in Task 1.

______________________________________________________________________

### Task 1: Declared configuration model and loader

**Files:**

- Create: `src/inspect_audit/findings/config.py`
- Create: `src/inspect_audit/findings/pilot.yaml`
- Modify: `src/inspect_audit/findings/adapters/__init__.py` (Context gains `config`)
- Modify: `pyproject.toml` (package data: include `pilot.yaml` alongside the existing taxonomies)
- Test: `tests/findings/test_config.py`

**Interfaces:**

- Produces:

  - `DatasetConfig(path: str | None, config: str | None, split: str | None, revision: str | None, fields: dict[str, str])`
  - `LogFilter(task_args: dict[str, JsonValue] | None = None, include_mock: bool = False)`
  - `EvalConfig(dataset: DatasetConfig | None = None, logs: LogFilter = LogFilter())`
  - `Config(defaults: EvalConfig = EvalConfig(), evals: dict[str, EvalConfig] = {})` with `for_eval(target: str) -> EvalConfig`
  - `load_config(path: Path) -> Config`
  - `DEFAULT_CONFIG_PATH: Path` pointing at the packaged `pilot.yaml`
  - `Context.config: Config` (default `Config()`)

- [ ] **Step 1: Write the failing tests**

```python
# tests/findings/test_config.py
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
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/findings/test_config.py -q --no-cov` Expected: FAIL with `ModuleNotFoundError: No module named 'inspect_audit.findings.config'`

- [ ] **Step 3: Write the config module**

```python
# src/inspect_audit/findings/config.py
"""What each eval's producers should examine, declared rather than inferred.

Auto-detection of dataset configuration and log relevance failed on five of six evals in the
acceptance sweep. The pilot declares these per eval; an eval without an entry gets `defaults`.
"""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, JsonValue

DEFAULT_CONFIG_PATH = Path(__file__).parent / "pilot.yaml"


class DatasetConfig(BaseModel):
    """Which dataset to scan. `path` falls back to eval.yaml's HuggingFace asset when None."""

    model_config = ConfigDict(extra="forbid")
    path: str | None = None
    config: str | None = None
    split: str | None = None
    revision: str | None = None
    # inspect-dataset field names by role: question, answer, id
    fields: dict[str, str] = Field(default_factory=dict)


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
        return self.evals.get(target, self.defaults)


def load_config(path: Path) -> Config:
    loaded = yaml.safe_load(path.read_text())
    if not isinstance(loaded, dict):
        raise ValueError(f"{path} must be a mapping with `defaults` and `evals`")
    return Config.model_validate(loaded)
```

- [ ] **Step 4: Write the packaged default**

```yaml
# src/inspect_audit/findings/pilot.yaml
# What the deterministic producers examine for each pilot eval. An eval without an entry
# uses `defaults`. Declare here whatever inference gets wrong; the run records what it used.
defaults:
  logs:
    include_mock: false

evals:
  inspect_evals/stereoset:
    dataset:
      path: McGill-NLP/stereoset
      config: intersentence
      split: validation
      fields:
        question: context
        answer: sentences
        id: id
```

- [ ] **Step 5: Add `config` to Context**

In `src/inspect_audit/findings/adapters/__init__.py`, add the import and field:

```python
from ..config import Config
```

and in `Context`:

```python
    resolve: bool = False
    config: Config = field(default_factory=Config)
```

- [ ] **Step 6: Make sure `pilot.yaml` ships in the wheel**

Check `pyproject.toml` `[tool.hatch.build.targets.wheel]`. It lists `packages = ["src/inspect_audit"]`, which includes non-Python files under the package, the same way `findings/taxonomies/*.json` already ship. Confirm with:

Run: `uv build --wheel -o /tmp/wheelcheck 2>/dev/null && unzip -l /tmp/wheelcheck/*.whl | grep pilot.yaml` Expected: one line naming `inspect_audit/findings/pilot.yaml`. If absent, add `include = ["src/inspect_audit/findings/pilot.yaml"]` under that table.

- [ ] **Step 7: Run the tests to verify they pass**

Run: `uv run pytest tests/findings/test_config.py -q --no-cov` Expected: 5 passed

- [ ] **Step 8: Commit**

```bash
git add src/inspect_audit/findings/config.py src/inspect_audit/findings/pilot.yaml src/inspect_audit/findings/adapters/__init__.py tests/findings/test_config.py pyproject.toml
git commit -m "feat(findings): declared per-eval configuration for what producers examine

pilot.yaml names the dataset to scan and which logs count for each pilot
eval; an eval without an entry gets the defaults. Context carries it.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

______________________________________________________________________

### Task 2: Dataset adapter takes its arguments from the declaration and records what it examined

**Files:**

- Modify: `src/inspect_audit/findings/adapters/dataset.py`
- Modify: `tests/findings/test_dataset_adapter.py`

**Interfaces:**

- Consumes: `Context.config`, `Config.for_eval`, `DatasetConfig` from Task 1.

- Produces: `scan_arguments(target: str, yaml_data: Mapping[str, Any], declared: DatasetConfig | None) -> tuple[str | None, list[str], dict[str, JsonValue]]` returning the dataset path, the extra CLI options, and the `examined` record written to `Run.inputs["dataset"]`. `DATASET_OVERRIDES` is removed.

- [ ] **Step 1: Write the failing tests**

Replace `test_overrides_include_stereoset` and extend the others in `tests/findings/test_dataset_adapter.py`:

```python
from inspect_audit.findings.adapters.dataset import (
    PRODUCER,
    hf_asset,
    parse,
    run,
    scan_arguments,
)
from inspect_audit.findings.config import Config, DatasetConfig, EvalConfig


def test_scan_arguments_prefer_the_declaration() -> None:
    declared = DatasetConfig(
        path="McGill-NLP/stereoset",
        config="intersentence",
        split="validation",
        revision="abc123",
        fields={"question": "context", "answer": "sentences", "id": "id"},
    )
    path, options, examined = scan_arguments("inspect_evals/stereoset", {}, declared)
    assert path == "McGill-NLP/stereoset"
    assert options == [
        "--config", "intersentence",
        "--split", "validation",
        "--revision", "abc123",
        "--question-field", "context",
        "--answer-field", "sentences",
        "--id-field", "id",
    ]
    assert examined == {
        "path": "McGill-NLP/stereoset",
        "config": "intersentence",
        "split": "validation",
        "revision": "abc123",
        "fields": {"question": "context", "answer": "sentences", "id": "id"},
        "declared": True,
    }


def test_scan_arguments_fall_back_to_eval_yaml_asset() -> None:
    yaml_data = {"external_assets": [{"type": "huggingface", "source": "a/b"}]}
    path, options, examined = scan_arguments("inspect_evals/x", yaml_data, None)
    assert (path, options) == ("a/b", [])
    assert examined["declared"] is False and examined["path"] == "a/b"


def test_scan_arguments_declared_without_path_uses_asset_for_path_only() -> None:
    yaml_data = {"external_assets": [{"type": "huggingface", "source": "a/b"}]}
    declared = DatasetConfig(split="test")
    path, options, examined = scan_arguments("inspect_evals/x", yaml_data, declared)
    assert path == "a/b" and options == ["--split", "test"]
    assert examined["declared"] is True and examined["path"] == "a/b"


def test_run_without_a_declaration_or_asset_is_a_skip_naming_both(tmp_path: Path) -> None:
    root = make_root(tmp_path)
    result = run("inspect_evals/stereoset", Context(ie_root=root))
    assert result.outcomes[0].status == "skip"
    message = result.outcomes[0].message or ""
    assert "pilot config" in message and "eval.yaml" in message


def test_run_with_a_stubbed_scan(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = make_root(tmp_path, extra=ASSET)
    monkeypatch.setenv("STUB_OUTPUT_DIR", str(FIXTURE))
    declared = DatasetConfig(
        config="intersentence",
        split="validation",
        fields={"question": "context", "answer": "sentences", "id": "id"},
    )
    ctx = Context(
        ie_root=root,
        out_dir=tmp_path / "out",
        producers=ProducerConfig(dataset=(sys.executable, str(STUBS / "echo_file.py"))),
        config=Config(evals={"inspect_evals/stereoset": EvalConfig(dataset=declared)}),
    )
    result = run("inspect_evals/stereoset", ctx)
    assert len(result.findings) == 46
    argv = result.inputs["argv"]
    assert isinstance(argv, list)
    assert "McGill-NLP/stereoset" in argv and "--config" in argv and "--question-field" in argv
    examined = result.inputs["dataset"]
    assert isinstance(examined, dict)
    assert examined["declared"] is True and examined["split"] == "validation"
```

Delete `test_overrides_include_stereoset` and the `DATASET_OVERRIDES` import.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/findings/test_dataset_adapter.py -q --no-cov` Expected: FAIL with `ImportError: cannot import name 'scan_arguments'`

- [ ] **Step 3: Implement `scan_arguments` and use it in `run`**

In `src/inspect_audit/findings/adapters/dataset.py`, remove the `DATASET_OVERRIDES` table and add:

```python
from pydantic import JsonValue

from ..config import DatasetConfig

_FIELD_OPTIONS = {"question": "--question-field", "answer": "--answer-field", "id": "--id-field"}


def scan_arguments(
    target: str, yaml_data: Mapping[str, Any], declared: DatasetConfig | None
) -> tuple[str | None, list[str], dict[str, JsonValue]]:
    """Dataset path, extra `inspect-dataset scan` options, and a record of what will be examined.

    The declaration wins where it speaks; eval.yaml's HuggingFace asset supplies the path
    otherwise. `declared` in the record says whether any declaration was consulted, so a
    consumer can tell an inferred scan from a configured one.
    """
    asset = hf_asset(yaml_data)
    path = (declared.path if declared and declared.path else None) or asset
    options: list[str] = []
    if declared:
        for option, value in (
            ("--config", declared.config),
            ("--split", declared.split),
            ("--revision", declared.revision),
        ):
            if value:
                options += [option, value]
        for role, name in declared.fields.items():
            if role in _FIELD_OPTIONS:
                options += [_FIELD_OPTIONS[role], name]
    examined: dict[str, JsonValue] = {
        "path": path,
        "config": declared.config if declared else None,
        "split": declared.split if declared else None,
        "revision": declared.revision if declared else None,
        "fields": dict(declared.fields) if declared else {},
        "declared": declared is not None,
    }
    return path, options, examined
```

Then rewrite the start of `run`:

```python
def run(target: str, ctx: Context) -> Run:
    """Scan the eval's dataset with static scanners into a temporary directory, then parse it."""
    timestamp = utcnow()
    declared = ctx.config.for_eval(target).dataset
    path, options, examined = scan_arguments(target, eval_yaml(ctx.ie_root, target), declared)
    if path is None:
        return skip_run(
            PRODUCER,
            target,
            ctx,
            "no dataset path: none declared in the pilot config and no huggingface asset in eval.yaml external_assets",
            timestamp=timestamp,
        )
    ctx.out_dir.mkdir(parents=True, exist_ok=True)
    scan_dir = Path(tempfile.mkdtemp(prefix="inspect_dataset_", dir=ctx.out_dir))
    argv = [*ctx.producers.dataset, "scan", path, *options, "-o", str(scan_dir)]
```

and pass the record through to `parse`:

```python
            inputs={"argv": argv, "scan_dir": str(scan_dir), "dataset": examined},
```

Delete the `overrides = DATASET_OVERRIDES.get(target, {})` block and its loop.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/findings/test_dataset_adapter.py -q --no-cov` Expected: all pass (the fixture-based `test_parse_summary_outcomes_and_findings` is unchanged)

- [ ] **Step 5: Run the whole findings suite; the CLI tests still pass because `--config` is not yet wired and `Context()` defaults to an empty `Config`, so the dataset scan falls back to the `ASSET` in eval.yaml with no options**

Run: `uv run pytest tests/findings -q --no-cov` Expected: all pass. If `test_run_writes_the_layout` fails on finding counts, the stub ignores argv, so the count is unchanged; investigate before proceeding.

- [ ] **Step 6: Commit**

```bash
git add src/inspect_audit/findings/adapters/dataset.py tests/findings/test_dataset_adapter.py
git commit -m "feat(findings): dataset scans take their arguments from the declaration and record what they examined

The per-eval override table is gone; pilot.yaml declares path, config,
split, revision and field roles, with eval.yaml's asset as the fallback
for the path. Run.inputs.dataset says what was scanned and whether it
was declared or inferred.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

______________________________________________________________________

### Task 3: Header matching respects qualified registry names

**Files:**

- Modify: `src/inspect_audit/findings/adapters/header.py` (`matching_headers`)
- Modify: `tests/findings/test_header_adapter.py`

**Interfaces:**

- Produces: `matching_headers(logs, target, names=None)` unchanged in signature; a header whose `task_registry_name` contains `/` matches only when its package prefix equals the target's and its tail is in `names`. Bare task names still match on the tail.

- [ ] **Step 1: Write the failing test**

The test helper `_log` builds an inline Task, whose header has no registry name. To test the qualified case, write a header-like object rather than a real log. Add to `tests/findings/test_header_adapter.py`:

```python
from inspect_audit.findings.adapters.header import _header_matches


class _Spec:
    def __init__(self, registry: str | None, task: str) -> None:
        self.task_registry_name, self.task = registry, task


def test_qualified_registry_names_match_only_their_own_package() -> None:
    names = {"scicode"}
    assert _header_matches(_Spec("inspect_evals/scicode", "scicode"), "inspect_evals/scicode", names)
    # the sample auditor's task over scicode is not scicode
    assert not _header_matches(
        _Spec("audit/inspect_evals/scicode", "scicode"), "inspect_evals/scicode", names
    )
    # a bare task name (a Task run from a file) still matches on the tail
    assert _header_matches(_Spec(None, "scicode"), "inspect_evals/scicode", names)
    # a multi-task package: registry name is the task, package prefix still has to agree
    assert _header_matches(
        _Spec("inspect_evals/lab_bench_litqa", "lab_bench_litqa"),
        "inspect_evals/lab_bench",
        {"lab_bench", "lab_bench_litqa"},
    )
    assert not _header_matches(
        _Spec("other_pkg/lab_bench_litqa", "lab_bench_litqa"),
        "inspect_evals/lab_bench",
        {"lab_bench", "lab_bench_litqa"},
    )
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/findings/test_header_adapter.py::test_qualified_registry_names_match_only_their_own_package -q --no-cov` Expected: FAIL with `ImportError: cannot import name '_header_matches'`

- [ ] **Step 3: Implement**

In `header.py`, add above `matching_headers`:

```python
def _header_matches(spec: Any, target: str, names: set[str]) -> bool:
    """Whether a header's task is one of `names` for this target.

    A qualified registry name (`pkg/task`) must share the target's package: the sample
    auditor's `audit/inspect_evals/scicode` is a run over scicode, not a run of it. A bare
    task name, as an inline Task records, matches on the tail alone.
    """
    registry = spec.task_registry_name
    if registry and "/" in registry:
        package, _, task_name = registry.rpartition("/")
        return package == target.rpartition("/")[0] and task_name in names
    return bool({_tail(registry), _tail(spec.task)} & names)
```

and change the loop body of `matching_headers` to:

```python
        if _header_matches(header.eval, target, wanted):
            matched.append((path, header))
```

Keep the existing `_tail` helper.

- [ ] **Step 4: Run the header tests to verify they pass**

Run: `uv run pytest tests/findings/test_header_adapter.py -q --no-cov` Expected: all pass

- [ ] **Step 5: Commit**

```bash
git add src/inspect_audit/findings/adapters/header.py tests/findings/test_header_adapter.py
git commit -m "fix(findings): a qualified registry name matches only its own package

audit/inspect_evals/scicode is the sample auditor run over scicode, not a
run of it; it no longer counts as a scicode log.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

______________________________________________________________________

### Task 4: Header adapter selects logs by the declared filter and records used and excluded

**Files:**

- Modify: `src/inspect_audit/findings/adapters/header.py`
- Modify: `tests/findings/test_header_adapter.py`
- Modify: `tests/findings/test_adapters_common.py` (shared `MOCK_OK` config for tests whose logs come from `mockllm/model`)

**Interfaces:**

- Consumes: `Context.config`, `LogFilter` from Task 1.

- Produces:

  - `select_headers(headers: Sequence[tuple[Path, EvalLog]], log_filter: LogFilter) -> tuple[list[tuple[Path, EvalLog]], list[dict[str, str]]]` returning used headers and `[{"path": ..., "reason": ...}]` exclusions.
  - `parse(...)` gains keyword `log_filter: LogFilter = LogFilter()` and writes `Run.inputs["logs"] = {"used": [...], "excluded": [...], "count_excluded": [...]}`; `count_excluded` lists logs used for drift and scoring checks but left out of the `dataset_samples` comparison because they carry non-default task arguments.
  - `MOCK_OK = Config(defaults=EvalConfig(logs=LogFilter(include_mock=True)))` in `tests/findings/test_adapters_common.py`.

- [ ] **Step 1: Add the shared test config**

In `tests/findings/test_adapters_common.py` add:

```python
from inspect_audit.findings.config import Config, EvalConfig, LogFilter

# the test logs are produced by mockllm/model, which the default filter excludes on purpose
MOCK_OK = Config(defaults=EvalConfig(logs=LogFilter(include_mock=True)))
```

- [ ] **Step 2: Write the failing tests**

In `tests/findings/test_header_adapter.py`, import `MOCK_OK` from `test_adapters_common` and `LogFilter` from `inspect_audit.findings.config`, then add:

```python
from inspect_audit.findings.adapters.header import select_headers


def test_mock_logs_are_excluded_unless_the_filter_allows_them(tmp_path: Path) -> None:
    log = _log(tmp_path / "logs")
    headers = matching_headers([log], "inspect_evals/stereoset")
    used, excluded = select_headers(headers, LogFilter())
    assert used == [] and len(excluded) == 1
    assert excluded[0]["path"] == str(log) and "mockllm/model" in excluded[0]["reason"]
    used, excluded = select_headers(headers, LogFilter(include_mock=True))
    assert len(used) == 1 and excluded == []


def test_task_args_filter_excludes_other_configurations(tmp_path: Path) -> None:
    default = _log(tmp_path / "a")
    variant = _log(tmp_path / "b", task_args={"subset": "small"})
    headers = matching_headers([default, variant], "inspect_evals/stereoset")
    used, excluded = select_headers(headers, LogFilter(task_args={}, include_mock=True))
    assert [p.name for p, _ in used] == [default.name]
    assert excluded[0]["path"] == str(variant) and "task args" in excluded[0]["reason"]


def test_all_logs_excluded_is_a_skip_that_says_why(tmp_path: Path) -> None:
    root = make_root(tmp_path)
    log = _log(tmp_path / "logs")
    result = run("inspect_evals/stereoset", Context(ie_root=root, logs=[log]))  # default config
    assert [o.status for o in result.outcomes] == ["skip"]
    message = result.outcomes[0].message or ""
    assert "1 matching log" in message and "excluded" in message and "mockllm" in message


def test_non_default_task_args_skip_the_sample_count_comparison_but_join_drift(
    tmp_path: Path,
) -> None:
    root = make_root(tmp_path)  # eval.yaml declares 2123
    default = _log(tmp_path / "a", samples=3, version=3)
    variant = _log(tmp_path / "b", samples=2, version=4, task_args={"subset": "small"})
    result = run("inspect_evals/stereoset", Context(ie_root=root, logs=[default, variant], config=MOCK_OK))
    counts = [f for f in result.findings if f.rule == "header.dataset_samples"]
    assert len(counts) == 1 and "3" in counts[0].summary  # only the default-args log is compared
    assert any(f.rule == "header.version_drift" for f in result.findings)  # both logs join drift
    logs = result.inputs["logs"]
    assert isinstance(logs, dict)
    assert len(logs["used"]) == 2 and logs["excluded"] == []
    assert logs["count_excluded"] == [{"path": str(variant), "reason": "task args {'subset': 'small'} differ from the default configuration"}]
```

Extend the `_log` helper so a log can carry task arguments. Inspect records `task_args` from the arguments passed to a registered task, so build the task through a decorated factory:

```python
from inspect_ai import Task, eval, task


def _log(
    log_dir: Path,
    *,
    name: str = "stereoset",
    version: int | str = 3,
    samples: int = 3,
    task_args: dict[str, str] | None = None,
) -> Path:
    @task(name=name)
    def _make(subset: str = "all") -> Task:
        return Task(
            name=name,
            dataset=MemoryDataset(
                [Sample(id=i, input=f"q{i}", target="ANSWER") for i in range(1, samples + 1)]
            ),
            scorer=match(),
            version=version,
        )

    return Path(
        eval(_make, task_args=task_args or {}, model="mockllm/model", log_dir=str(log_dir), display="none")[0]
        .location.removeprefix("file://")
    )
```

Then update every existing test in the file that calls `run(..., Context(ie_root=root, logs=...))` to pass `config=MOCK_OK`, since their logs are mock runs. There are eight such calls (lines with `Context(ie_root=root, logs=`). `test_no_logs_is_a_skip` keeps its assertion but also needs `config=MOCK_OK` so its skip is "no logs" rather than "all excluded".

- [ ] **Step 3: Run the tests to verify they fail**

Run: `uv run pytest tests/findings/test_header_adapter.py -q --no-cov` Expected: FAIL with `ImportError: cannot import name 'select_headers'`. If `_log` fails first with a registry error about the `@task` decorator inside a function, register with a unique name per call: `@task(name=f"{name}")` is fine because Inspect allows re-registration of the same name; if it complains, fall back to `Task(...)` for the no-args case and use the factory only when `task_args` is given.

- [ ] **Step 4: Implement `select_headers` and the recorded inputs**

In `header.py`, import `LogFilter`:

```python
from ..config import LogFilter
```

Add after `matching_headers`:

```python
def _is_mock(header: EvalLog) -> bool:
    return str(header.eval.model or "").startswith("mockllm/")


def select_headers(
    headers: Sequence[tuple[Path, EvalLog]], log_filter: LogFilter
) -> tuple[list[tuple[Path, EvalLog]], list[dict[str, str]]]:
    """Split matched headers into the logs this eval's checks use and the ones left out, with reasons."""
    used: list[tuple[Path, EvalLog]] = []
    excluded: list[dict[str, str]] = []
    for path, header in headers:
        if _is_mock(header) and not log_filter.include_mock:
            excluded.append({"path": str(path), "reason": f"mock model {header.eval.model}"})
        elif log_filter.task_args is not None and dict(header.eval.task_args or {}) != log_filter.task_args:
            excluded.append(
                {"path": str(path), "reason": f"task args {dict(header.eval.task_args or {})} differ from the configured {log_filter.task_args}"}
            )
        else:
            used.append((path, header))
    return used, excluded
```

Change `parse` to take the filter and to compare sample counts only over default-configuration logs:

```python
def parse(
    headers: Sequence[tuple[Path, EvalLog]],
    target: str,
    subject: Subject,
    yaml_data: Mapping[str, Any],
    *,
    timestamp: datetime,
    resolved_ids: set[str] | None = None,
    log_filter: LogFilter = LogFilter(),
    excluded: Sequence[Mapping[str, str]] = (),
) -> Run:
```

Inside `parse`, before the `dataset_samples` loop:

```python
    # a run with non-default task arguments may legitimately use a different dataset size;
    # compare against eval.yaml's declared count only where the arguments are the defaults
    count_excluded: list[dict[str, str]] = []
    countable: list[tuple[Path, EvalLog]] = []
    for path, header in headers:
        args = dict(header.eval.task_args or {})
        if log_filter.task_args is None and args:
            count_excluded.append(
                {"path": str(path), "reason": f"task args {args} differ from the default configuration"}
            )
        else:
            countable.append((path, header))
```

and change that loop's `for path, header in headers:` to `for path, header in countable:`. Leave the drift, unscored, dirty and unknown-id loops over `headers`.

Change the returned `Run.inputs` to:

```python
        inputs={
            "logs": {
                "used": [str(path) for path, _ in headers],
                "excluded": [dict(entry) for entry in excluded],
                "count_excluded": count_excluded,
            },
            "comparison": {
                "commit": subject.revision.commit,
                "package_version": subject.revision.package_version,
                "task_version": subject.task_version.full if subject.task_version else None,
            },
        },
```

Rewrite `run`:

```python
def run(target: str, ctx: Context) -> Run:
    """Read the headers of the logs that match `target`, keep the ones the eval's filter allows, run the checks."""
    timestamp = utcnow()
    yaml_data = eval_yaml(ctx.ie_root, target)
    matched = matching_headers(ctx.logs, target, task_names(yaml_data, target))
    if not matched:
        return skip_run(
            PRODUCER,
            target,
            ctx,
            f"no logs for target {target} among {len(ctx.logs)} file(s)",
            timestamp=timestamp,
        )
    log_filter = ctx.config.for_eval(target).logs
    headers, excluded = select_headers(matched, log_filter)
    if not headers:
        reasons = sorted({entry["reason"] for entry in excluded})
        return skip_run(
            PRODUCER,
            target,
            ctx,
            f"{len(matched)} matching log(s), all excluded: {'; '.join(reasons)}",
            timestamp=timestamp,
        )
    resolved, reason = _resolved_ids(target, headers) if ctx.resolve else (None, None)
    result = parse(
        headers,
        target,
        subject_for(target, ctx),
        yaml_data,
        timestamp=timestamp,
        resolved_ids=resolved,
        log_filter=log_filter,
        excluded=excluded,
    )
    if ctx.resolve and resolved is None:
        result.outcomes.append(
            Outcome(rule="header.unknown_sample_ids", status="skip", message=reason)
        )
    return result
```

The skip message for "1 matching log(s), all excluded: mock model mockllm/model" satisfies the test's three substrings.

- [ ] **Step 5: Run the header tests to verify they pass**

Run: `uv run pytest tests/findings/test_header_adapter.py -q --no-cov` Expected: all pass

- [ ] **Step 6: Run the whole findings suite**

Run: `uv run pytest tests/findings -q --no-cov` Expected: the CLI tests that supply mock logs now fail, because the default config excludes them and the header run becomes a skip (exit code 1 and different finding counts). That is Task 5's job; note the failing test names and continue.

- [ ] **Step 7: Commit**

```bash
git add src/inspect_audit/findings/adapters/header.py tests/findings/test_header_adapter.py tests/findings/test_adapters_common.py
git commit -m "feat(findings): header checks use only the logs the eval's filter allows, and say which were left out

Mock-model runs are excluded unless the pilot config allows them; a
declared task_args filter excludes other configurations; logs with
non-default arguments join drift and scoring checks but not the sample
count comparison. Run.inputs.logs records used, excluded and
count_excluded with reasons, and an all-excluded set is a skip that
says why.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

______________________________________________________________________

### Task 5: CLI loads the configuration

**Files:**

- Modify: `src/inspect_audit/findings/cli.py`
- Create: `tests/findings/fixtures/pilot.yaml`
- Modify: `tests/findings/test_cli.py`

**Interfaces:**

- Consumes: `load_config`, `DEFAULT_CONFIG_PATH`, `Config` from Task 1.

- Produces: `run --config PATH` (default `DEFAULT_CONFIG_PATH`); `Context(config=...)` populated; a malformed config is a usage error (exit 2) naming the file.

- [ ] **Step 1: Write the test fixture and the failing tests**

```yaml
# tests/findings/fixtures/pilot.yaml
defaults:
  logs:
    include_mock: true
evals:
  inspect_evals/stereoset:
    dataset:
      config: intersentence
      split: validation
      fields: {question: context, answer: sentences, id: id}
```

In `tests/findings/test_cli.py`, make `_stubbed_env` also return the config flag and thread it through every `main(["run", ...])` call that supplies `--logs`:

```python
PILOT = FIXTURES / "pilot.yaml"


def _run_args(root: Path, out: Path, *extra: str) -> list[str]:
    return ["run", "--root", str(root), "--out", str(out), "--config", str(PILOT), *extra]
```

and replace the argument lists in `test_run_writes_the_layout`, `test_run_with_a_failing_producer_exits_one_and_records_a_skip`, `test_producers_flag_selects_external_producers_only`, `test_summary_regenerates_identical_files`, `test_hawk_logs_source_is_downloaded_into_the_cache`, `test_hawk_task_flag_resolves_sets_by_task` and `test_reruns_keep_earlier_runs_and_a_partial_sweep_keeps_other_producers_current` with `_run_args(root, out, "--logs", str(log), "inspect_evals/stereoset")` and the equivalents. Add:

```python
def test_default_config_excludes_mock_logs_so_header_is_a_skip(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = make_root(tmp_path, extra=ASSET)
    log = _log(tmp_path / "logs")
    _stubbed_env(monkeypatch)
    out = tmp_path / "out"
    code = main(["run", "--root", str(root), "--logs", str(log), "--out", str(out), "inspect_evals/stereoset"])
    assert code == 1  # the header producer skipped: its only log is a mock run
    summary = (out / "inspect-evals-stereoset" / "SUMMARY.md").read_text()
    assert "all excluded" in summary and "mockllm" in summary


def test_malformed_config_is_a_usage_error(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root = make_root(tmp_path, extra=ASSET)
    bad = tmp_path / "bad.yaml"
    bad.write_text("evals:\n  inspect_evals/stereoset:\n    dataset:\n      pth: x\n")
    code = main(["run", "--root", str(root), "--out", str(tmp_path / "out"), "--config", str(bad), "inspect_evals/stereoset"])
    assert code == 2
    assert "bad.yaml" in capsys.readouterr().err
```

- [ ] **Step 2: Run the CLI tests to verify they fail**

Run: `uv run pytest tests/findings/test_cli.py -q --no-cov` Expected: FAIL with `argparse` error `unrecognized arguments: --config`

- [ ] **Step 3: Implement**

In `cli.py`, import:

```python
from pydantic import ValidationError

from .config import DEFAULT_CONFIG_PATH, load_config
```

Add to the `run` subparser after `--resolve`:

```python
    run_p.add_argument(
        "--config",
        type=Path,
        default=DEFAULT_CONFIG_PATH,
        help="per-eval declaration of what to scan and which logs count (default: the packaged pilot.yaml)",
    )
```

In `main`, before building `Context`:

```python
    try:
        config = load_config(args.config)
    except (OSError, ValueError, ValidationError) as ex:
        print(f"could not load {args.config}: {ex}", file=sys.stderr)
        return 2
```

(`ValidationError` is a `ValueError` subclass; listing it makes the intent visible.) Then pass `config=config` to `Context(...)`.

- [ ] **Step 4: Run the CLI tests and the whole findings suite**

Run: `uv run pytest tests/findings -q --no-cov` Expected: all pass

- [ ] **Step 5: Commit**

```bash
git add src/inspect_audit/findings/cli.py tests/findings/test_cli.py tests/findings/fixtures/pilot.yaml
git commit -m "feat(findings): --config selects the per-eval declaration; the packaged pilot.yaml is the default

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

______________________________________________________________________

### Task 6: Summaries show what was examined and what was left out

**Files:**

- Modify: `src/inspect_audit/findings/render.py`
- Modify: `tests/findings/test_render.py`

**Interfaces:**

- Consumes: `Run.inputs["dataset"]` (Task 2), `Run.inputs["logs"]` and `Run.inputs["comparison"]` (Task 4).

- Produces: `render_eval_summary` emits an `## Inputs` section between the subject table and Outcomes; `render_sweep_summary` cells carry `(n logs, m excluded)` for header runs.

- [ ] **Step 1: Write the failing tests**

In `tests/findings/test_render.py` add (the `run` fixture is the lint run from `conftest.py`):

```python
from inspect_audit.findings.models import Outcome, Run


def _header_run(run: Run) -> Run:
    return run.model_copy(
        update={
            "id": "header-1",
            "producer": "inspect_audit_header",
            "findings": [],
            "outcomes": [Outcome(rule="header.dataset_samples", status="pass")],
            "inputs": {
                "logs": {
                    "used": ["/logs/a.eval", "/logs/b.eval"],
                    "excluded": [{"path": "/logs/mock.eval", "reason": "mock model mockllm/model"}],
                    "count_excluded": [
                        {"path": "/logs/b.eval", "reason": "task args {'subset': 'small'} differ from the default configuration"}
                    ],
                },
                "comparison": {"commit": "5687c5cdf", "package_version": "0.21.1", "task_version": "3-A"},
            },
        }
    )


def _dataset_run(run: Run) -> Run:
    return run.model_copy(
        update={
            "id": "dataset-1",
            "producer": "inspect_dataset",
            "findings": [],
            "inputs": {
                "dataset": {
                    "path": "McGill-NLP/stereoset",
                    "config": "intersentence",
                    "split": "validation",
                    "revision": None,
                    "fields": {"question": "context"},
                    "declared": True,
                }
            },
        }
    )


def test_eval_summary_has_an_inputs_section(run: Run) -> None:
    text = render_eval_summary([run, _header_run(run), _dataset_run(run)])
    inputs = text.split("## Inputs", 1)[1].split("## Outcomes", 1)[0]
    assert "McGill-NLP/stereoset" in inputs and "intersentence" in inputs and "declared" in inputs
    assert "2 log(s) used" in inputs
    assert "1 excluded" in inputs and "mock model mockllm/model" in inputs
    assert "1 not compared for sample count" in inputs and "subset" in inputs
    assert "compared against 5687c5cdf" in inputs and "3-A" in inputs


def test_eval_summary_inputs_section_says_when_nothing_was_declared(run: Run) -> None:
    text = render_eval_summary([run])
    inputs = text.split("## Inputs", 1)[1].split("## Outcomes", 1)[0]
    assert "no dataset scan" in inputs and "no logs" in inputs


def test_sweep_summary_shows_log_counts(run: Run) -> None:
    text = render_sweep_summary({"inspect_evals/stereoset": [run, _header_run(run)]})
    assert "inspect_audit_header: 0 findings (2 logs, 1 excluded)" in text
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/findings/test_render.py -q --no-cov` Expected: FAIL with `IndexError: list index out of range` on the `split("## Inputs")` (no such section yet)

- [ ] **Step 3: Implement**

In `render.py` add helpers:

```python
from typing import Any


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _inputs_lines(runs: Sequence[Run]) -> list[str]:
    """What the producers examined, and what they left out, as bullet lines."""
    lines: list[str] = []
    dataset = next((_dict(run.inputs.get("dataset")) for run in runs if "dataset" in run.inputs), {})
    if dataset:
        where = ", ".join(
            f"{key} {dataset[key]}" for key in ("config", "split", "revision") if dataset.get(key)
        )
        origin = "declared in the pilot config" if dataset.get("declared") else "inferred from eval.yaml"
        lines.append(f"- Dataset scanned: `{dataset.get('path')}`{f' ({where})' if where else ''}, {origin}.")
    else:
        lines.append("- Dataset: no dataset scan ran.")
    logs = next((_dict(run.inputs.get("logs")) for run in runs if "logs" in run.inputs), {})
    if logs:
        used, excluded, count_excluded = _list(logs.get("used")), _list(logs.get("excluded")), _list(logs.get("count_excluded"))
        lines.append(f"- Logs: {len(used)} log(s) used, {len(excluded)} excluded, {len(count_excluded)} not compared for sample count.")
        for entry in excluded:
            lines.append(f"  - excluded `{_dict(entry).get('path')}`: {_dict(entry).get('reason')}")
        for entry in count_excluded:
            lines.append(f"  - not compared `{_dict(entry).get('path')}`: {_dict(entry).get('reason')}")
    else:
        lines.append("- Logs: no logs examined.")
    comparison = next((_dict(run.inputs.get("comparison")) for run in runs if "comparison" in run.inputs), {})
    if comparison:
        lines.append(
            f"- Header checks compared against {comparison.get('commit') or comparison.get('package_version')}"
            f", task version {comparison.get('task_version')}."
        )
    return lines
```

In `render_eval_summary`, after the subject table and the skipped line, insert:

```python
    parts += ["## Inputs", "", *_inputs_lines(runs), ""]
```

In `render_sweep_summary`, change the non-skipped cell to:

```python
                n = len(run.findings)
                cell = f"{run.producer}: {n} finding{'' if n == 1 else 's'}"
                logs = _dict(run.inputs.get("logs"))
                if logs:
                    used, excluded = len(_list(logs.get("used"))), len(_list(logs.get("excluded")))
                    cell += f" ({used} log{'' if used == 1 else 's'}, {excluded} excluded)"
                cells.append(cell)
```

- [ ] **Step 4: Run the render tests and the whole findings suite**

Run: `uv run pytest tests/findings -q --no-cov` Expected: all pass. `test_summary_regenerates_identical_files` in the CLI tests still passes because rendering stays deterministic.

- [ ] **Step 5: Commit**

```bash
git add src/inspect_audit/findings/render.py tests/findings/test_render.py
git commit -m "feat(findings): summaries show what was examined and what was left out

An Inputs section per eval names the dataset scanned and whether it was
declared, the logs used and excluded with reasons, and the revision the
header checks compared against. The sweep table carries log counts.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

______________________________________________________________________

### Task 7: Docs and the gate

**Files:**

- Modify: `docs/findings-cli.md`

- Modify: `docs/superpowers/specs/2026-09-25-findings-prototype-design.md` (Dataset, Header, CLI sections)

- Modify: `docs/roadmap.md` (milestone 1 first bullet: mark input selection done)

- [ ] **Step 1: Update the CLI doc**

In `docs/findings-cli.md`, replace the "Limitations today" paragraph with:

```markdown
`--config PATH` names the per-eval declaration of what to scan and which logs count; the default is the packaged `pilot.yaml`. Each entry can declare the dataset path, config, split, revision and field roles, a `task_args` filter for logs, and whether mock-model runs count (they do not by default). An eval without an entry falls back to `eval.yaml`'s HuggingFace asset for the dataset and uses every non-mock log it matches. Every run records what it examined under `inputs`, and each eval's `SUMMARY.md` has an Inputs section listing the dataset scanned, the logs used and excluded with reasons, and the revision the header checks compared against.

Limitations today: dataset scans need a declaration or an `eval.yaml` asset and skip otherwise; the header checks compare against the checkout's `eval.yaml`; no suppressions or issue links are applied yet. See [roadmap.md](roadmap.md) and the [design spec](superpowers/specs/2026-09-25-findings-prototype-design.md).
```

- [ ] **Step 2: Update the spec**

In the `### Dataset` section of the design spec, replace the sentence about `DATASET_OVERRIDES` (search for "override") with:

```markdown
Scan arguments come from `pilot.yaml` (`findings/config.py`: `DatasetConfig` with path, config, split, revision and field roles), falling back to `eval.yaml`'s HuggingFace asset for the path. `Run.inputs.dataset` records what was scanned and whether it was declared or inferred. With neither a declaration nor an asset the producer skips, naming both places it looked.
```

In `### Header`, after the paragraph on matching, add:

```markdown
A qualified registry name must share the target's package: `audit/inspect_evals/scicode` is not a scicode log. Matched logs then pass the eval's `LogFilter`: mock-model runs are excluded unless `include_mock`, and a declared `task_args` excludes other configurations. Logs with non-default arguments still join the drift, unscored and dirty checks but are not compared against the declared sample count. `Run.inputs.logs` records `used`, `excluded` and `count_excluded` with reasons; when every matched log is excluded the run is a skip that says why.
```

In `## CLI`, add a bullet:

```markdown
- `--config PATH` (default: the packaged `findings/pilot.yaml`) declares per eval what to scan and which logs count. A file that fails validation is a usage error naming the file.
```

In `## Outputs`, after the per-eval `SUMMARY.md` description, add: "An Inputs section lists the dataset scanned and its origin, logs used and excluded with reasons, and the comparison revision; the sweep table shows log counts per header run."

- [ ] **Step 3: Tick the roadmap**

In `docs/roadmap.md`, milestone 1, change the first bullet's opening to `- Input selection (done 2026-09-30, PR pending).` and leave the rest of the sentence as is.

- [ ] **Step 4: Run the full gate**

Run: `uv run pre-commit run --all-files && uv run basedpyright src && uv run pytest -q -p no:cacheprovider` Expected: pre-commit clean (run it twice if mdformat or end-of-file-fixer modify files, then stage the changes), basedpyright 0 errors, whole suite passes.

- [ ] **Step 5: Commit**

```bash
git add docs
git commit -m "docs(findings): declared configuration, log selection and the Inputs section

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

- [ ] **Step 6: Try it on real inputs before opening the PR**

Run, from the worktree, against the local inspect_evals checkout and the pulled Hawk logs:

```bash
uv run inspect-audit-findings run --root ../../../inspect_evals --out /tmp/pilot-out \
  --logs artefacts/hawk/logs/hle-physics-sol-high-20260917 inspect_evals/hle inspect_evals/stereoset
sed -n '1,40p' /tmp/pilot-out/inspect-evals-hle/SUMMARY.md
```

Expected: the HLE Inputs section shows the physics log used (its model is not mock) and stereoset's dataset scan reports `declared in the pilot config`. If `artefacts/hawk` is not present in this worktree, run `uv run inspect-audit-findings hawk-pull` first (needs `hawk login`). Nothing from `/tmp/pilot-out` is committed.

______________________________________________________________________

## Self-review notes

- Spec coverage: roadmap milestone 1 "Input selection" has three sentences. Declared dataset configuration and recording what was examined: Tasks 1, 2. Partition logs by task and arguments, distinguish benchmark attempts from mock and sample-audit runs, name the comparison revision: Tasks 3, 4 (comparison revision already recorded by PR #12). Skips and unsupported inputs render beside findings: Task 6.
- Type consistency: `select_headers` returns `list[dict[str, str]]` for exclusions and `parse` accepts `Sequence[Mapping[str, str]]`; `Run.inputs` is `dict[str, JsonValue]`, and the nested dicts and lists written here are JSON-compatible. `scan_arguments` returns `dict[str, JsonValue]` for `examined`; `dict(declared.fields)` is `dict[str, str]`, which is JSON-compatible.
- Review Focus items 1 to 5 map to tests in Tasks 3, 2, 4, 4, 1 respectively.
- Not in this plan, on purpose: grouping by rule, suppressions and `issues.yaml` (next plan), and the chess bundle import (the plan after).
