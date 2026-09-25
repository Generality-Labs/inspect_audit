# Findings Prototype Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A `findings` module in inspect_audit that turns the output of three deterministic producers (inspect-evals-lint, inspect-dataset, Inspect log headers) into one finding schema, with a CLI that runs them over named inspect_evals evals and writes JSON, parquet and markdown summaries.

**Architecture:** Pydantic v2 envelope models with the producer's record kept verbatim under `source`. One adapter per producer, each split into a pure `parse()` tested on fixtures and a `run()` that shells out through a configurable command prefix and never raises. A CLI orchestrates producers per eval and a deterministic renderer writes summaries from the run files.

**Tech Stack:** Python 3.13, pydantic v2, pandas + pyarrow (already dependencies), PyYAML (already transitively present), inspect_ai log API, argparse, pytest. Producers run as subprocesses via `uvx`.

**Spec:** `docs/superpowers/specs/2026-09-25-findings-prototype-design.md`

## Global Constraints

- Work in the worktree `/Users/matt/Developer/inspect_ai/inspect_audit/.worktrees/findings-prototype` on branch `findings-prototype`. Never `cd` to the main checkout. Never use bare `git stash`.
- Environment: `uv sync --python 3.13 --extra dev` already run. Run tests with `uv run pytest tests/findings -q` and the full gate with `make check && uv run pytest -m "not docker" -q`. Both must stay green after every task.
- mypy runs `--strict` over `src`. Every function in `src/inspect_audit/findings/` is fully annotated. No `Any` leaks in public signatures except where the spec says `JsonValue` or `dict[str, Any]` for verbatim records.
- ruff rules: `E, W, F, D, I, B, SIM101, PLE`, google docstring convention, `D10` (missing docstrings) and `E501` ignored. Docstrings that exist must be one summary line ending in a full stop, optionally followed by a blank line and body.
- Files under `findings/` other than `cli.py` and `adapters/header.py` import nothing from `inspect_audit` outside `findings/`. `cli.py` may import `inspect_audit._registry.fetch_logs`. `adapters/header.py` may import `inspect_audit._resolve.resolve_task` inside the `--resolve` code path only.
- `Dimension` literal values, exact: `construct, contentvalidity, dataset, scaffold, harness, environment, grading, resources, informativeness`.
- `Severity` values, exact: `none, minor, major, critical`. `Status` values, exact: `hypothesis, supported, qualified, retracted`.
- Pinned producer specs, exact: `inspect-evals-lint==0.7.0`; `git+https://github.com/Generality-Labs/inspect_dataset@afbc94c0b509`.
- Environment overrides, exact names: `INSPECT_AUDIT_LINT_CMD`, `INSPECT_AUDIT_DATASET_CMD`.
- Commit messages: imperative summary line, optional body, ending with the trailer `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`. One logical change per commit. Do not push.
- Fixtures come from `/Users/matt/Developer/inspect_ai/inspect_audit/agent_artefacts/deterministic_pass/stereoset/` (read-only source; copy into `tests/findings/fixtures/`). `answer_length.json` (5 MB) is not copied.

## Review Focus

1. A lint JSON document with more than one package (a run over several evals at once) must produce one `Run` per package or refuse clearly, never merge diagnostics under the first package's subject. Test added to Task 5.
2. A `.eval` whose `eval.task_registry_name` is `None` (older logs, or tasks run from a file) must still match on `eval.task` and never crash the header adapter. Test added to Task 7.
3. An `eval.yaml` with several tasks must compare `dataset_samples` for the task matching the log's unqualified task name, not the first entry. Test added to Task 7.
4. A producer stub that prints valid JSON but exits non-zero must yield a skip run, not a parsed run; the exit code is authoritative. Test added to Task 5.
5. A `Finding` deserialised from JSON with an unknown `kind` in `locations` must fail validation with a clear error rather than silently becoming a base location. Test added to Task 1.

---

### Task 1: Models, fingerprint, schema files

**Files:**
- Create: `src/inspect_audit/findings/__init__.py`
- Create: `src/inspect_audit/findings/models.py`
- Create: `src/inspect_audit/findings/fingerprint.py`
- Create: `src/inspect_audit/findings/schema/finding.schema.json`
- Create: `src/inspect_audit/findings/schema/run.schema.json`
- Create: `tests/findings/__init__.py`
- Create: `tests/findings/conftest.py`
- Create: `tests/findings/test_models.py`
- Create: `tests/findings/test_fingerprint.py`
- Modify: `docs/superpowers/specs/2026-09-25-findings-prototype-design.md` (add `producer` and `rule` to `Finding`)

**Interfaces:**
- Produces: every model name below, `AnyLocation`, `Dimension`, `Severity`, `Status`, `fingerprint(producer: str, rule: str, eval: str, primary: Location) -> str`, `FINGERPRINT_VERSION: int`, `TaskVersion.parse(text: str) -> TaskVersion`, `utcnow() -> datetime`.

- [ ] **Step 1: Amend the spec**

The spec's `Finding` omits the two fields the fingerprint is computed from. Edit `docs/superpowers/specs/2026-09-25-findings-prototype-design.md`, in the `**Finding.**` paragraph, replacing `` `fingerprint_version: int`, `subject` `` with `` `fingerprint_version: int`, `producer: str`, `rule: str`, `subject` ``. Also in the `Outputs` section the parquet columns already list `producer` and `rule`; leave them.

- [ ] **Step 2: Write the failing model tests**

`tests/findings/__init__.py` is empty.

`tests/findings/conftest.py`:

```python
"""Shared factories for findings tests."""

from datetime import UTC, datetime

import pytest

from inspect_audit.findings.models import (
    CodeLocation,
    Finding,
    Revision,
    Run,
    Source,
    Subject,
    TaskVersion,
)


@pytest.fixture
def subject() -> Subject:
    return Subject(
        eval="inspect_evals/stereoset",
        revision=Revision(commit="5687c5cdf", package_version="0.21.1.dev24+g5687c5cdf", dirty=False),
        task_version=TaskVersion.parse("3-A"),
    )


@pytest.fixture
def finding(subject: Subject) -> Finding:
    return Finding(
        fingerprint="sha256:0",
        producer="inspect_evals_lint",
        rule="IEBP008",
        subject=subject,
        dimension="dataset",
        severity="minor",
        status="supported",
        summary="filter_duplicate_ids() without max_duplicates= or reason=",
        locations=[
            CodeLocation(role="primary", file="src/inspect_evals/stereoset/stereoset.py", line=64, column=15)
        ],
        run_id="lint-1",
        source=Source(format="inspect_evals_lint.Diagnostic@0.7.0", record={"code": "IEBP008"}),
    )


@pytest.fixture
def run(subject: Subject, finding: Finding) -> Run:
    return Run(
        id="lint-1",
        timestamp=datetime(2026, 9, 25, 4, 20, 50, tzinfo=UTC),
        producer="inspect_evals_lint",
        producer_version="0.7.0",
        subject=subject,
        findings=[finding],
    )
```

`tests/findings/test_models.py`:

```python
"""The envelope models: shape, validation and the committed JSON schema."""

import json
import os
from pathlib import Path
from typing import get_args

import pytest
from pydantic import ValidationError

import inspect_audit
from inspect_audit._assessment import framework_definitions
from inspect_audit.findings import models
from inspect_audit.findings.models import (
    CodeLocation,
    Dimension,
    Finding,
    LogLocation,
    Revision,
    Run,
    SampleLocation,
    Source,
    Subject,
    TaskVersion,
    TranscriptLocation,
)

SCHEMA_DIR = Path(models.__file__).parent / "schema"


def test_run_round_trips_through_json(run: Run) -> None:
    again = Run.model_validate_json(run.model_dump_json())
    assert again == run
    assert again.findings[0].primary_location.key() == "code:src/inspect_evals/stereoset/stereoset.py:64"


def test_location_union_discriminates_on_kind() -> None:
    data = {
        "kind": "transcript", "role": "primary", "eval_id": "E", "sample_uuid": "U", "message_id": "M",
    }
    location = Finding.model_validate(
        {
            "fingerprint": "sha256:0", "producer": "p", "rule": "r",
            "subject": {"eval": "inspect_evals/x", "revision": {"commit": "abc"}},
            "dimension": "grading", "severity": "none", "status": "hypothesis", "summary": "s",
            "locations": [data], "run_id": "run", "source": {"format": "f", "record": None},
        }
    ).locations[0]
    assert isinstance(location, TranscriptLocation)
    assert location.key() == "transcript:E:U:M"


def test_unknown_location_kind_is_rejected(finding: Finding) -> None:
    data = finding.model_dump()
    data["locations"][0]["kind"] = "planet"
    with pytest.raises(ValidationError, match="kind"):
        Finding.model_validate(data)


def test_exactly_one_primary_location(finding: Finding) -> None:
    data = finding.model_dump(mode="json")
    data["locations"] = [CodeLocation(file="a.py").model_dump(mode="json")]
    with pytest.raises(ValidationError, match="exactly one primary"):
        Finding.model_validate(data)
    data["locations"] = [
        finding.locations[0].model_dump(mode="json"),
        CodeLocation(role="primary", file="b.py").model_dump(mode="json"),
    ]
    with pytest.raises(ValidationError, match="exactly one primary"):
        Finding.model_validate(data)


def test_revision_needs_commit_or_package_version() -> None:
    with pytest.raises(ValidationError, match="commit or package_version"):
        Revision()
    assert Revision(package_version="0.21.1").commit is None


@pytest.mark.parametrize(
    ("text", "comparability", "interface"),
    [("3-A", 3, "A"), ("4", 4, None), ("2.0.0", None, None), ("v1-B", None, "B")],
)
def test_task_version_parse(text: str, comparability: int | None, interface: str | None) -> None:
    parsed = TaskVersion.parse(text)
    assert parsed.full == text
    assert parsed.comparability == comparability
    assert parsed.interface == interface


def test_location_keys() -> None:
    assert SampleLocation(dataset="d", sample_id="s").key() == "sample:d:s"
    assert LogLocation(eval_id="E", path="eval.dataset.samples").key() == "log:E:eval.dataset.samples"
    assert CodeLocation(file="f.py").key() == "code:f.py:0"


def test_locations_allow_extra_keys() -> None:
    location = CodeLocation.model_validate({"kind": "code", "file": "f.py", "snippet": "x = 1"})
    assert location.model_dump()["snippet"] == "x = 1"


def test_dimension_matches_the_framework() -> None:
    report = Path(inspect_audit.__file__).parent / "investigation" / "report"
    parsed = {entry["dimension"] for entry in framework_definitions(report).values()}
    assert parsed == set(get_args(Dimension))


@pytest.mark.parametrize(("name", "model"), [("finding.schema.json", Finding), ("run.schema.json", Run)])
def test_schema_files_are_current(name: str, model: type[Finding] | type[Run]) -> None:
    expected = json.dumps(model.model_json_schema(), indent=2, sort_keys=True) + "\n"
    path = SCHEMA_DIR / name
    if os.environ.get("INSPECT_AUDIT_UPDATE_SCHEMA"):
        path.write_text(expected)
    assert path.read_text() == expected, f"run INSPECT_AUDIT_UPDATE_SCHEMA=1 pytest {__file__} to regenerate {name}"


def test_source_keeps_record_verbatim() -> None:
    record = {"nested": {"list": [1, 2, {"deep": None}]}, "text": "x"}
    source = Source(format="f@1", record=record)
    assert Source.model_validate_json(source.model_dump_json()).record == record


def test_subject_defaults(subject: Subject) -> None:
    assert subject.dataset is None
    assert subject.task_args == {}
```

`tests/findings/test_fingerprint.py`:

```python
"""The fingerprint recipe is the identity contract; pin it."""

from hashlib import sha256

from inspect_audit.findings.fingerprint import FINGERPRINT_VERSION, fingerprint
from inspect_audit.findings.models import CodeLocation


def test_recipe_is_pinned() -> None:
    primary = CodeLocation(role="primary", file="src/inspect_evals/stereoset/stereoset.py", line=64)
    recipe = "inspect_evals_lint|IEBP008|inspect_evals/stereoset|code:src/inspect_evals/stereoset/stereoset.py:64"
    assert fingerprint("inspect_evals_lint", "IEBP008", "inspect_evals/stereoset", primary) == (
        "sha256:" + sha256(recipe.encode()).hexdigest()
    )
    assert FINGERPRINT_VERSION == 1


def test_every_component_changes_the_value() -> None:
    a = CodeLocation(file="a.py", line=1)
    base = fingerprint("p", "r", "e", a)
    assert fingerprint("q", "r", "e", a) != base
    assert fingerprint("p", "s", "e", a) != base
    assert fingerprint("p", "r", "f", a) != base
    assert fingerprint("p", "r", "e", CodeLocation(file="a.py", line=2)) != base


def test_related_fields_do_not_change_the_value() -> None:
    a = CodeLocation(file="a.py", line=1, column=3, quote="x")
    b = CodeLocation(file="a.py", line=1, column=9, quote="y", role="primary")
    assert fingerprint("p", "r", "e", a) == fingerprint("p", "r", "e", b)
```

- [ ] **Step 3: Run the tests to see them fail**

Run: `uv run pytest tests/findings -q`
Expected: collection errors, `ModuleNotFoundError: No module named 'inspect_audit.findings'`.

- [ ] **Step 4: Write the models**

`src/inspect_audit/findings/__init__.py`:

```python
"""A finding schema for eval audits: thin envelope, verbatim producer record."""

from .fingerprint import FINGERPRINT_VERSION, fingerprint
from .models import (
    AnyLocation,
    ArtifactLocation,
    CodeLocation,
    DatasetRef,
    Dimension,
    Effect,
    Finding,
    Location,
    LogLocation,
    Outcome,
    Provenance,
    Revision,
    Run,
    SampleLocation,
    ScorerLocation,
    Severity,
    Source,
    Status,
    StatusChange,
    Subject,
    Suppression,
    TaskVersion,
    TranscriptLocation,
    UrlLocation,
    VersionRef,
    utcnow,
)

__all__ = [
    "FINGERPRINT_VERSION",
    "AnyLocation",
    "ArtifactLocation",
    "CodeLocation",
    "DatasetRef",
    "Dimension",
    "Effect",
    "Finding",
    "Location",
    "LogLocation",
    "Outcome",
    "Provenance",
    "Revision",
    "Run",
    "SampleLocation",
    "ScorerLocation",
    "Severity",
    "Source",
    "Status",
    "StatusChange",
    "Subject",
    "Suppression",
    "TaskVersion",
    "TranscriptLocation",
    "UrlLocation",
    "VersionRef",
    "fingerprint",
    "utcnow",
]
```

`src/inspect_audit/findings/models.py`:

```python
"""Envelope models. Ten fields carry the contract; `source` carries the producer's record verbatim."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Annotated, Literal

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    computed_field,
    model_validator,
)

Dimension = Literal[
    "construct",
    "contentvalidity",
    "dataset",
    "scaffold",
    "harness",
    "environment",
    "grading",
    "resources",
    "informativeness",
]
Severity = Literal["none", "minor", "major", "critical"]
Status = Literal["hypothesis", "supported", "qualified", "retracted"]
Role = Literal["primary", "related"]


def utcnow() -> datetime:
    """Now, timezone-aware, to the second."""
    return datetime.now(UTC).replace(microsecond=0)


class Location(BaseModel):
    """Where a finding points. Subclasses fix `kind` and define `key()`."""

    model_config = ConfigDict(extra="allow")
    role: Role = "related"
    quote: str | None = None

    def key(self) -> str:
        """The comparable string the fingerprint hashes."""
        raise NotImplementedError


class CodeLocation(Location):
    kind: Literal["code"] = "code"
    file: str
    line: int | None = None
    end_line: int | None = None
    column: int | None = None

    def key(self) -> str:
        return f"code:{self.file}:{self.line or 0}"


class SampleLocation(Location):
    kind: Literal["sample"] = "sample"
    dataset: str
    sample_id: str
    field: str | None = None

    def key(self) -> str:
        return f"sample:{self.dataset}:{self.sample_id}"


class TranscriptLocation(Location):
    kind: Literal["transcript"] = "transcript"
    eval_id: str
    sample_uuid: str
    message_id: str | None = None
    event_uuid: str | None = None
    sample_id: str | None = None
    epoch: int | None = None

    def key(self) -> str:
        return f"transcript:{self.eval_id}:{self.sample_uuid}:{self.message_id or self.event_uuid or ''}"


class LogLocation(Location):
    kind: Literal["log"] = "log"
    eval_id: str
    path: str
    location_hint: str | None = None

    def key(self) -> str:
        return f"log:{self.eval_id}:{self.path}"


class ScorerLocation(Location):
    kind: Literal["scorer"] = "scorer"
    eval_id: str
    scorer: str

    def key(self) -> str:
        return f"scorer:{self.eval_id}:{self.scorer}"


class ArtifactLocation(Location):
    kind: Literal["artifact"] = "artifact"
    path: str
    location: str | None = None

    def key(self) -> str:
        return f"artifact:{self.path}:{self.location or ''}"


class UrlLocation(Location):
    kind: Literal["url"] = "url"
    url: str

    def key(self) -> str:
        return f"url:{self.url}"


AnyLocation = Annotated[
    CodeLocation
    | SampleLocation
    | TranscriptLocation
    | LogLocation
    | ScorerLocation
    | ArtifactLocation
    | UrlLocation,
    Field(discriminator="kind"),
]


class Provenance(BaseModel):
    """Who did something, when and why; mirrors inspect_ai's ProvenanceData."""

    model_config = ConfigDict(extra="forbid")
    timestamp: AwareDatetime
    author: str
    reason: str | None = None
    metadata: dict[str, JsonValue] = Field(default_factory=dict)


class Suppression(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: str
    provenance: Provenance


class StatusChange(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: Status
    provenance: Provenance


class Effect(BaseModel):
    model_config = ConfigDict(extra="forbid")
    affected: int | None = None
    denominator: int | None = None
    score: Literal["measured", "hypothesised"] | None = None
    description: str | None = None


class VersionRef(BaseModel):
    model_config = ConfigDict(extra="forbid")
    commit: str | None = None
    comparability_version: int | None = None


class Revision(BaseModel):
    """The code examined: a commit, an installed version, or both."""

    model_config = ConfigDict(extra="forbid")
    commit: str | None = None
    package_version: str | None = None
    dirty: bool | None = None

    @model_validator(mode="after")
    def _one_identity(self) -> Revision:
        if self.commit is None and self.package_version is None:
            raise ValueError("a revision needs a commit or package_version")
        return self


_TASK_VERSION = re.compile(r"^(?P<comparability>\d+)(?:-(?P<interface>[A-Za-z]+))?$")


class TaskVersion(BaseModel):
    """inspect_evals' `N-X` task version, kept whole and split."""

    model_config = ConfigDict(extra="forbid")
    full: str
    comparability: int | None = None
    interface: str | None = None

    @classmethod
    def parse(cls, text: str) -> TaskVersion:
        """Split `3-A` into comparability 3 and interface A; anything else keeps only `full`."""
        match = _TASK_VERSION.match(text.strip())
        if match is None:
            interface = re.search(r"-([A-Za-z]+)$", text.strip())
            return cls(full=text, interface=interface.group(1) if interface else None)
        return cls(
            full=text,
            comparability=int(match.group("comparability")),
            interface=match.group("interface"),
        )


class DatasetRef(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str | None = None
    config: str | None = None
    split: str | None = None
    revision: str | None = None


class Subject(BaseModel):
    """What the finding is about."""

    model_config = ConfigDict(extra="forbid")
    eval: str
    revision: Revision
    task_version: TaskVersion | None = None
    dataset: DatasetRef | None = None
    task_args: dict[str, JsonValue] = Field(default_factory=dict)


class Source(BaseModel):
    """The producer's own record, untouched, plus the log header when there is one."""

    model_config = ConfigDict(extra="forbid")
    format: str
    record: JsonValue
    eval_spec: dict[str, JsonValue] | None = None


class Outcome(BaseModel):
    model_config = ConfigDict(extra="forbid")
    rule: str
    status: Literal["pass", "fail", "skip"]
    message: str | None = None


class Finding(BaseModel):
    """One defect record. See docs/finding-schema-envelope.md."""

    model_config = ConfigDict(extra="forbid")
    schema_version: Literal["0.1"] = "0.1"
    fingerprint: str
    fingerprint_version: int = 1
    producer: str
    rule: str
    subject: Subject
    dimension: Dimension
    severity: Severity
    status: Status
    summary: str = Field(min_length=1)
    locations: list[AnyLocation] = Field(min_length=1)
    run_id: str
    source: Source
    aliases: list[str] = Field(default_factory=list)
    suppressions: list[Suppression] = Field(default_factory=list)
    history: list[StatusChange] = Field(default_factory=list)
    introduced: VersionRef | None = None
    fixed: VersionRef | None = None
    effect: Effect | None = None

    @model_validator(mode="after")
    def _one_primary(self) -> Finding:
        primaries = [location for location in self.locations if location.role == "primary"]
        if len(primaries) != 1:
            raise ValueError(f"a finding needs exactly one primary location, got {len(primaries)}")
        return self

    @computed_field  # type: ignore[prop-decorator]
    @property
    def primary_location(self) -> AnyLocation:
        return next(location for location in self.locations if location.role == "primary")


class Run(BaseModel):
    """One producer invocation over one eval: provenance, pass/fail/skip outcomes, findings."""

    model_config = ConfigDict(extra="forbid")
    id: str
    timestamp: AwareDatetime
    producer: str
    producer_version: str | None = None
    git_commit: str | None = None
    subject: Subject
    inputs: dict[str, JsonValue] = Field(default_factory=dict)
    model: str | None = None
    cost_usd: float | None = None
    duration_s: float | None = None
    outcomes: list[Outcome] = Field(default_factory=list)
    findings: list[Finding] = Field(default_factory=list)
```

`src/inspect_audit/findings/fingerprint.py`:

```python
"""The identity recipe. Computed once at write time and stored; never recomputed on read."""

from hashlib import sha256

from .models import Location

FINGERPRINT_VERSION = 1


def fingerprint(producer: str, rule: str, eval: str, primary: Location) -> str:
    """Stable identity of a finding across runs: producer, rule, eval and primary location key."""
    recipe = f"{producer}|{rule}|{eval}|{primary.key()}"
    return "sha256:" + sha256(recipe.encode()).hexdigest()
```

- [ ] **Step 5: Generate the schema files and run the tests**

Run: `mkdir -p src/inspect_audit/findings/schema && INSPECT_AUDIT_UPDATE_SCHEMA=1 uv run pytest tests/findings/test_models.py::test_schema_files_are_current -q`
Expected: PASS, and two new files under `schema/`.

Run: `uv run pytest tests/findings -q`
Expected: all PASS. If `test_dimension_matches_the_framework` fails, the `Dimension` literal is wrong, not the framework; fix the literal to match the parsed set.

- [ ] **Step 6: Lint and type-check**

Run: `make check`
Expected: `All checks passed!` and `Success: no issues found`. If mypy complains about `computed_field` with `@property`, keep the `# type: ignore[prop-decorator]` shown.

- [ ] **Step 7: Commit**

```bash
git add src/inspect_audit/findings tests/findings docs/superpowers/specs
git commit -m "$(cat <<'EOF'
Add the findings envelope models and fingerprint

Ten envelope fields plus a verbatim producer record, a discriminated
Location union with per-kind keys, and a pinned fingerprint recipe.
The Dimension literal is asserted against the GL framework parser.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
EOF
)"
```

---

### Task 2: IO

**Files:**
- Create: `src/inspect_audit/findings/io.py`
- Create: `tests/findings/test_io.py`
- Modify: `src/inspect_audit/findings/__init__.py` (export the IO functions)

**Interfaces:**
- Consumes: `Run`, `Finding` from Task 1.
- Produces: `write_run(run: Run, path: Path) -> Path`, `read_run(path: Path) -> Run`, `read_runs(root: Path) -> list[Run]`, `findings_df(runs: Sequence[Run]) -> pd.DataFrame`, `runs_df(runs: Sequence[Run]) -> pd.DataFrame`, `write_parquet(frame: pd.DataFrame, path: Path) -> Path`, `FINDING_COLUMNS: tuple[str, ...]`.

- [ ] **Step 1: Write the failing tests**

`tests/findings/test_io.py`:

```python
"""Runs on disk and as dataframes."""

from pathlib import Path

import pandas as pd

from inspect_audit.findings.io import (
    FINDING_COLUMNS,
    findings_df,
    read_run,
    read_runs,
    runs_df,
    write_parquet,
    write_run,
)
from inspect_audit.findings.models import Outcome, Run


def test_write_and_read_a_run(tmp_path: Path, run: Run) -> None:
    path = write_run(run, tmp_path / "stereoset" / "lint.run.json")
    assert path.name == "lint.run.json"
    assert read_run(path) == run


def test_read_runs_walks_the_tree(tmp_path: Path, run: Run) -> None:
    write_run(run, tmp_path / "a" / "lint.run.json")
    write_run(run.model_copy(update={"id": "lint-2"}), tmp_path / "b" / "lint.run.json")
    (tmp_path / "b" / "notes.json").write_text("{}")
    assert sorted(r.id for r in read_runs(tmp_path)) == ["lint-1", "lint-2"]


def test_findings_df_columns_and_values(run: Run) -> None:
    frame = findings_df([run])
    assert list(frame.columns) == list(FINDING_COLUMNS)
    row = frame.iloc[0]
    assert row["fingerprint"] == "sha256:0"
    assert row["subject_eval"] == "inspect_evals/stereoset"
    assert row["subject_revision_commit"] == "5687c5cdf"
    assert row["subject_task_version_full"] == "3-A"
    assert row["primary_kind"] == "code"
    assert row["primary_key"] == "code:src/inspect_evals/stereoset/stereoset.py:64"
    assert row["producer"] == "inspect_evals_lint"
    assert row["rule"] == "IEBP008"
    assert '"code": "IEBP008"' in row["source"]
    assert row["locations"].startswith("[")


def test_findings_df_is_empty_but_typed_without_findings(run: Run) -> None:
    frame = findings_df([run.model_copy(update={"findings": []})])
    assert list(frame.columns) == list(FINDING_COLUMNS)
    assert len(frame) == 0


def test_runs_df_counts_outcomes(run: Run) -> None:
    run = run.model_copy(
        update={
            "outcomes": [Outcome(rule="a", status="pass"), Outcome(rule="b", status="fail"), Outcome(rule="c", status="skip")],
            "duration_s": 1.5,
        }
    )
    frame = runs_df([run])
    row = frame.iloc[0]
    assert (row["outcomes_pass"], row["outcomes_fail"], row["outcomes_skip"]) == (1, 1, 1)
    assert row["findings"] == 1
    assert not bool(row["skipped"])
    assert row["duration_s"] == 1.5


def test_parquet_round_trip(tmp_path: Path, run: Run) -> None:
    path = write_parquet(findings_df([run]), tmp_path / "findings.parquet")
    again = pd.read_parquet(path)
    assert list(again.columns) == list(FINDING_COLUMNS)
    assert again.iloc[0]["fingerprint"] == "sha256:0"
```

- [ ] **Step 2: Run the tests to see them fail**

Run: `uv run pytest tests/findings/test_io.py -q`
Expected: `ModuleNotFoundError: No module named 'inspect_audit.findings.io'`.

- [ ] **Step 3: Write the IO module**

`src/inspect_audit/findings/io.py`:

```python
"""Runs as JSON files, findings as dataframes and parquet."""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pandas as pd

from .models import Run

FINDING_COLUMNS: tuple[str, ...] = (
    "fingerprint",
    "fingerprint_version",
    "schema_version",
    "subject_eval",
    "subject_revision_commit",
    "subject_revision_package_version",
    "subject_task_version_full",
    "subject_dataset_path",
    "dimension",
    "severity",
    "status",
    "summary",
    "primary_kind",
    "primary_key",
    "run_id",
    "producer",
    "rule",
    "source_format",
    "source",
    "locations",
)

RUN_COLUMNS: tuple[str, ...] = (
    "run_id",
    "producer",
    "producer_version",
    "subject_eval",
    "timestamp",
    "duration_s",
    "outcomes_pass",
    "outcomes_fail",
    "outcomes_skip",
    "findings",
    "skipped",
)


def write_run(run: Run, path: Path) -> Path:
    """Write one run as indented JSON, creating parent directories."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(run.model_dump_json(indent=1) + "\n")
    return path


def read_run(path: Path) -> Run:
    return Run.model_validate_json(path.read_text())


def read_runs(root: Path) -> list[Run]:
    """Every `*.run.json` under `root`, sorted by path."""
    return [read_run(path) for path in sorted(root.rglob("*.run.json"))]


def _finding_records(runs: Sequence[Run]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for run in runs:
        for finding in run.findings:
            primary = finding.primary_location
            records.append(
                {
                    "fingerprint": finding.fingerprint,
                    "fingerprint_version": finding.fingerprint_version,
                    "schema_version": finding.schema_version,
                    "subject_eval": finding.subject.eval,
                    "subject_revision_commit": finding.subject.revision.commit,
                    "subject_revision_package_version": finding.subject.revision.package_version,
                    "subject_task_version_full": finding.subject.task_version.full if finding.subject.task_version else None,
                    "subject_dataset_path": finding.subject.dataset.path if finding.subject.dataset else None,
                    "dimension": finding.dimension,
                    "severity": finding.severity,
                    "status": finding.status,
                    "summary": finding.summary,
                    "primary_kind": primary.kind,
                    "primary_key": primary.key(),
                    "run_id": finding.run_id,
                    "producer": finding.producer,
                    "rule": finding.rule,
                    "source_format": finding.source.format,
                    "source": finding.source.model_dump_json(),
                    "locations": json.dumps([location.model_dump(mode="json") for location in finding.locations]),
                }
            )
    return records


def findings_df(runs: Sequence[Run]) -> pd.DataFrame:
    """One row per finding, envelope flattened, source and locations as JSON strings."""
    return pd.DataFrame.from_records(_finding_records(runs), columns=list(FINDING_COLUMNS))


def runs_df(runs: Sequence[Run]) -> pd.DataFrame:
    """One row per run with outcome counts."""
    records: list[dict[str, Any]] = []
    for run in runs:
        statuses = [outcome.status for outcome in run.outcomes]
        records.append(
            {
                "run_id": run.id,
                "producer": run.producer,
                "producer_version": run.producer_version,
                "subject_eval": run.subject.eval,
                "timestamp": run.timestamp.isoformat(),
                "duration_s": run.duration_s,
                "outcomes_pass": statuses.count("pass"),
                "outcomes_fail": statuses.count("fail"),
                "outcomes_skip": statuses.count("skip"),
                "findings": len(run.findings),
                "skipped": bool(statuses) and all(status == "skip" for status in statuses),
            }
        )
    return pd.DataFrame.from_records(records, columns=list(RUN_COLUMNS))


def write_parquet(frame: pd.DataFrame, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_parquet(path, index=False)
    return path
```

Add to `__init__.py` imports and `__all__`: `FINDING_COLUMNS, RUN_COLUMNS, findings_df, read_run, read_runs, runs_df, write_parquet, write_run` from `.io`.

- [ ] **Step 4: Run the tests and the gate**

Run: `uv run pytest tests/findings -q && make check`
Expected: all PASS, checks clean. If pandas-stubs complains about `from_records` typing, annotate `records` as `list[dict[str, Any]]` (already done).

- [ ] **Step 5: Commit**

```bash
git add src/inspect_audit/findings tests/findings
git commit -m "$(cat <<'EOF'
Add findings IO: run JSON files, dataframes and parquet

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
EOF
)"
```

---

### Task 3: Render

**Files:**
- Create: `src/inspect_audit/findings/render.py`
- Create: `tests/findings/test_render.py`
- Modify: `src/inspect_audit/findings/__init__.py`

**Interfaces:**
- Consumes: `Run`, `Finding`, `Outcome`.
- Produces: `render_eval_summary(runs: Sequence[Run]) -> str`, `render_sweep_summary(runs_by_eval: Mapping[str, Sequence[Run]]) -> str`, `NOISE_THRESHOLD: int = 100`.

- [ ] **Step 1: Write the failing tests**

`tests/findings/test_render.py`:

```python
"""Deterministic markdown from runs; no model, byte-stable."""

from inspect_audit.findings.models import Finding, Outcome, Run, SampleLocation, Source
from inspect_audit.findings.render import NOISE_THRESHOLD, render_eval_summary, render_sweep_summary


def _noisy(run: Run, count: int) -> Run:
    base = run.findings[0]
    noisy = [
        base.model_copy(
            update={
                "producer": "inspect_dataset",
                "rule": "answer_length",
                "severity": "none",
                "locations": [SampleLocation(role="primary", dataset="d", sample_id=str(i))],
                "fingerprint": f"sha256:{i}",
                "source": Source(format="inspect_dataset.Finding@0.4.0", record={"i": i}),
            }
        )
        for i in range(count)
    ]
    return run.model_copy(update={"id": "dataset-1", "producer": "inspect_dataset", "findings": noisy})


def test_eval_summary_has_subject_outcomes_and_findings(run: Run) -> None:
    run = run.model_copy(update={"outcomes": [Outcome(rule="IEBP008", status="fail", message="dup filter")]})
    text = render_eval_summary([run])
    assert text.startswith("# inspect_evals/stereoset\n")
    assert "| Revision | 5687c5cdf" in text
    assert "| Task version | 3-A" in text
    assert "| inspect_evals_lint | IEBP008 | fail | dup filter |" in text
    assert "| inspect_evals_lint | 1 |" in text
    assert "| dataset | minor | 1 |" in text
    assert "- minor · dataset · IEBP008 · `code:src/inspect_evals/stereoset/stereoset.py:64` · filter_duplicate_ids() without max_duplicates= or reason=" in text


def test_eval_summary_flags_noisy_rules(run: Run) -> None:
    text = render_eval_summary([run, _noisy(run, NOISE_THRESHOLD + 1)])
    assert f"answer_length produced {NOISE_THRESHOLD + 1} findings" in text
    assert text.count("- none · dataset · answer_length") == NOISE_THRESHOLD + 1


def test_eval_summary_marks_a_skipped_producer(run: Run) -> None:
    skipped = run.model_copy(
        update={"id": "dataset-1", "producer": "inspect_dataset", "findings": [],
                "outcomes": [Outcome(rule="inspect_dataset", status="skip", message="no huggingface asset")]}
    )
    text = render_eval_summary([run, skipped])
    assert "| inspect_dataset | inspect_dataset | skip | no huggingface asset |" in text
    assert "inspect_dataset: skipped" in text


def test_sweep_summary_one_row_per_eval(run: Run) -> None:
    other = run.model_copy(update={"id": "lint-2", "subject": run.subject.model_copy(update={"eval": "inspect_evals/hle"}), "findings": []})
    text = render_sweep_summary({"inspect_evals/hle": [other], "inspect_evals/stereoset": [run]})
    lines = [line for line in text.splitlines() if line.startswith("| inspect_evals/")]
    assert lines[0].startswith("| inspect_evals/hle |")
    assert lines[1].startswith("| inspect_evals/stereoset |")
    assert "| inspect_evals/stereoset | inspect_evals_lint: 1 finding" in text


def test_rendering_is_deterministic(run: Run) -> None:
    assert render_eval_summary([run]) == render_eval_summary([run])
```

- [ ] **Step 2: Run the tests to see them fail**

Run: `uv run pytest tests/findings/test_render.py -q`
Expected: `ModuleNotFoundError`.

- [ ] **Step 3: Write the renderer**

`src/inspect_audit/findings/render.py`:

```python
"""Markdown summaries from runs, by string templating. No model calls."""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence

from .models import Finding, Run

NOISE_THRESHOLD = 100

_SEVERITY_ORDER = {"critical": 0, "major": 1, "minor": 2, "none": 3}


def _table(headers: Sequence[str], rows: Sequence[Sequence[str]]) -> str:
    head = "| " + " | ".join(headers) + " |"
    rule = "|" + "---|" * len(headers)
    body = ["| " + " | ".join(row) + " |" for row in rows]
    return "\n".join([head, rule, *body])


def _sorted_findings(findings: Sequence[Finding]) -> list[Finding]:
    return sorted(findings, key=lambda f: (_SEVERITY_ORDER[f.severity], f.rule, f.primary_location.key()))


def _finding_line(finding: Finding) -> str:
    return (
        f"- {finding.severity} · {finding.dimension} · {finding.rule} · "
        f"`{finding.primary_location.key()}` · {finding.summary}"
    )


def render_eval_summary(runs: Sequence[Run]) -> str:
    """One eval: subject, outcomes across producers, counts, then every finding by producer."""
    runs = sorted(runs, key=lambda r: (r.producer, r.id))
    subject = runs[0].subject
    parts: list[str] = [f"# {subject.eval}", ""]

    subject_rows = [
        ["Revision", subject.revision.commit or "", subject.revision.package_version or ""],
        ["Task version", subject.task_version.full if subject.task_version else "", ""],
        ["Dataset", subject.dataset.path if subject.dataset and subject.dataset.path else "",
         subject.dataset.revision if subject.dataset and subject.dataset.revision else ""],
    ]
    parts += [_table(["Subject", "", ""], subject_rows), ""]

    skipped = [r.producer for r in runs if r.outcomes and all(o.status == "skip" for o in r.outcomes)]
    if skipped:
        parts += [", ".join(f"{producer}: skipped" for producer in skipped), ""]

    outcome_rows = [
        [run.producer, outcome.rule, outcome.status, outcome.message or ""]
        for run in runs
        for outcome in run.outcomes
        if outcome.status != "pass"
    ]
    passes = sum(1 for run in runs for outcome in run.outcomes if outcome.status == "pass")
    parts += ["## Outcomes", "", f"{passes} passing outcome(s) not listed.", ""]
    if outcome_rows:
        parts += [_table(["Producer", "Rule", "Status", "Message"], outcome_rows), ""]

    by_producer = Counter(f.producer for run in runs for f in run.findings)
    parts += ["## Findings by producer", "",
              _table(["Producer", "Findings"], [[p, str(n)] for p, n in sorted(by_producer.items())]), ""]
    by_dimension = Counter((f.dimension, f.severity) for run in runs for f in run.findings)
    parts += ["## Findings by dimension and severity", "",
              _table(["Dimension", "Severity", "Findings"],
                     [[d, s, str(n)] for (d, s), n in sorted(by_dimension.items(), key=lambda kv: (kv[0][0], _SEVERITY_ORDER[kv[0][1]]))]),
              ""]

    by_rule = Counter((f.producer, f.rule) for run in runs for f in run.findings)
    noisy = [(p, r, n) for (p, r), n in sorted(by_rule.items()) if n > NOISE_THRESHOLD]
    if noisy:
        parts += ["## Noise", ""]
        parts += [f"- {p}: {r} produced {n} findings; likely a rule that does not fit this eval." for p, r, n in noisy]
        parts.append("")

    parts += ["## Findings", ""]
    for run in runs:
        parts += [f"### {run.producer} ({run.id})", ""]
        if not run.findings:
            parts += ["No findings.", ""]
            continue
        parts += [_finding_line(f) for f in _sorted_findings(run.findings)]
        parts.append("")
    return "\n".join(parts).rstrip() + "\n"


def render_sweep_summary(runs_by_eval: Mapping[str, Sequence[Run]]) -> str:
    """Every eval on one line: per-producer finding counts, skips called out."""
    rows: list[list[str]] = []
    for eval_name in sorted(runs_by_eval):
        cells: list[str] = []
        for run in sorted(runs_by_eval[eval_name], key=lambda r: r.producer):
            if run.outcomes and all(o.status == "skip" for o in run.outcomes):
                cells.append(f"{run.producer}: skipped")
            else:
                n = len(run.findings)
                cells.append(f"{run.producer}: {n} finding{'' if n == 1 else 's'}")
        rows.append([eval_name, "; ".join(cells)])
    return "\n".join(["# Sweep summary", "", _table(["Eval", "Producers"], rows), ""])
```

Export `render_eval_summary`, `render_sweep_summary`, `NOISE_THRESHOLD` from `__init__.py`.

- [ ] **Step 4: Run the tests and the gate**

Run: `uv run pytest tests/findings -q && make check`
Expected: PASS and clean.

- [ ] **Step 5: Commit**

```bash
git add src/inspect_audit/findings tests/findings
git commit -m "$(cat <<'EOF'
Render per-eval and sweep summaries from runs

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
EOF
)"
```

---

### Task 4: Producer configuration and shared adapter helpers

**Files:**
- Create: `src/inspect_audit/findings/producers.py`
- Create: `src/inspect_audit/findings/adapters/__init__.py`
- Create: `tests/findings/test_adapters_common.py`
- Create: `tests/findings/stubs/__init__.py` (empty)
- Create: `tests/findings/stubs/echo_file.py`
- Create: `tests/findings/stubs/fail.py`

**Interfaces:**
- Consumes: models from Task 1.
- Produces: `ProducerConfig` with `lint: tuple[str, ...]`, `dataset: tuple[str, ...]`, `timeout_s: float`, `ProducerConfig.from_env(env: Mapping[str, str] | None = None) -> ProducerConfig`; `LINT_SPEC`, `DATASET_SPEC`; in `adapters/__init__.py`: `Context` dataclass (`ie_root: Path`, `logs: list[Path]`, `out_dir: Path`, `producers: ProducerConfig`, `resolve: bool = False`), `ProducerError(Exception)`, `CommandResult` (`returncode: int`, `stdout: str`, `stderr: str`), `run_command(argv: Sequence[str], *, timeout: float, cwd: Path | None = None) -> CommandResult`, `package_of(target: str) -> str`, `eval_yaml(ie_root: Path, target: str) -> dict[str, Any]`, `repo_revision(ie_root: Path) -> Revision`, `task_version_from_yaml(data: Mapping[str, Any]) -> TaskVersion | None`, `subject_for(target: str, ctx: Context) -> Subject`, `new_run_id(producer: str, target: str, timestamp: datetime) -> str`, `skip_run(producer: str, target: str, ctx: Context, message: str, *, timestamp: datetime | None = None) -> Run`, `slug(text: str) -> str`.

- [ ] **Step 1: Write the stubs and the failing tests**

`tests/findings/stubs/echo_file.py`:

```python
"""Stub producer: print the file named by STUB_OUTPUT_FILE to stdout, ignore arguments.

For `-o <dir>` style producers, when STUB_OUTPUT_DIR is set, copy that directory's
contents into the directory following `-o` instead.
"""

import os
import shutil
import sys
from pathlib import Path

if "STUB_OUTPUT_DIR" in os.environ and "-o" in sys.argv:
    target = Path(sys.argv[sys.argv.index("-o") + 1])
    shutil.copytree(os.environ["STUB_OUTPUT_DIR"], target, dirs_exist_ok=True)
else:
    sys.stdout.write(Path(os.environ["STUB_OUTPUT_FILE"]).read_text())
sys.exit(int(os.environ.get("STUB_EXIT", "0")))
```

`tests/findings/stubs/fail.py`:

```python
"""Stub producer that fails."""

import sys

sys.stderr.write("boom: simulated producer failure\n")
sys.exit(1)
```

`tests/findings/test_adapters_common.py`:

```python
"""Producer configuration, subject derivation and the never-raise command runner."""

import subprocess
import sys
from pathlib import Path

import pytest

from inspect_audit.findings.adapters import (
    Context,
    ProducerError,
    eval_yaml,
    new_run_id,
    package_of,
    repo_revision,
    run_command,
    skip_run,
    slug,
    subject_for,
    task_version_from_yaml,
)
from inspect_audit.findings.producers import DATASET_SPEC, LINT_SPEC, ProducerConfig

STUBS = Path(__file__).parent / "stubs"


def make_root(tmp_path: Path, *, version: str = "3-A", extra: str = "") -> Path:
    root = tmp_path / "ie"
    package = root / "src" / "inspect_evals" / "stereoset"
    package.mkdir(parents=True)
    (package / "eval.yaml").write_text(
        f"title: StereoSet\nversion: \"{version}\"\ntasks:\n  - name: stereoset\n    dataset_samples: 2123\n{extra}"
    )
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    subprocess.run(["git", "-C", str(root), "-c", "user.email=t@t", "-c", "user.name=t", "add", "."], check=True)
    subprocess.run(["git", "-C", str(root), "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "init"], check=True)
    return root


def make_ctx(root: Path, tmp_path: Path) -> Context:
    return Context(ie_root=root, logs=[], out_dir=tmp_path / "out", producers=ProducerConfig())


def test_default_prefixes_pin_the_specs() -> None:
    config = ProducerConfig()
    assert config.lint == ("uvx", "--from", LINT_SPEC, "inspect-evals-lint")
    assert config.dataset == ("uvx", "--from", DATASET_SPEC, "inspect-dataset")
    assert LINT_SPEC == "inspect-evals-lint==0.7.0"
    assert DATASET_SPEC == "git+https://github.com/Generality-Labs/inspect_dataset@afbc94c0b509"


def test_env_overrides_are_shell_split() -> None:
    config = ProducerConfig.from_env({"INSPECT_AUDIT_LINT_CMD": "python '/tmp/my stub.py' --flag"})
    assert config.lint == ("python", "/tmp/my stub.py", "--flag")
    assert config.dataset == ProducerConfig().dataset


def test_package_of_and_slug() -> None:
    assert package_of("inspect_evals/stereoset") == "stereoset"
    assert package_of("stereoset") == "stereoset"
    assert slug("inspect_evals/SWE Lancer") == "inspect-evals-swe-lancer"


def test_eval_yaml_and_task_version(tmp_path: Path) -> None:
    root = make_root(tmp_path)
    data = eval_yaml(root, "inspect_evals/stereoset")
    assert data["tasks"][0]["dataset_samples"] == 2123
    parsed = task_version_from_yaml(data)
    assert parsed is not None and parsed.comparability == 3 and parsed.interface == "A"
    assert eval_yaml(root, "inspect_evals/missing") == {}
    assert task_version_from_yaml({}) is None


def test_repo_revision_reads_git(tmp_path: Path) -> None:
    root = make_root(tmp_path)
    revision = repo_revision(root)
    assert revision.commit is not None and len(revision.commit) >= 7
    assert revision.dirty is False
    (root / "untracked.txt").write_text("x")
    assert repo_revision(root).dirty is True


def test_repo_revision_without_git_uses_package_version(tmp_path: Path) -> None:
    revision = repo_revision(tmp_path)
    assert revision.commit is None
    assert revision.package_version is not None  # inspect_evals is not installed here; the fallback is "unknown"


def test_subject_for(tmp_path: Path) -> None:
    root = make_root(tmp_path)
    subject = subject_for("inspect_evals/stereoset", make_ctx(root, tmp_path))
    assert subject.eval == "inspect_evals/stereoset"
    assert subject.task_version is not None and subject.task_version.full == "3-A"


def test_run_id_is_unique_per_producer_target_time() -> None:
    from datetime import UTC, datetime

    stamp = datetime(2026, 9, 25, 4, 20, 50, tzinfo=UTC)
    assert new_run_id("inspect_evals_lint", "inspect_evals/stereoset", stamp) == "inspect_evals_lint-20260925T042050Z-inspect-evals-stereoset"


def test_skip_run(tmp_path: Path) -> None:
    root = make_root(tmp_path)
    run = skip_run("inspect_dataset", "inspect_evals/stereoset", make_ctx(root, tmp_path), "no huggingface asset")
    assert run.findings == []
    assert [(o.rule, o.status, o.message) for o in run.outcomes] == [("inspect_dataset", "skip", "no huggingface asset")]


def test_run_command_captures_output(tmp_path: Path) -> None:
    payload = tmp_path / "payload.txt"
    payload.write_text("hello")
    result = run_command([sys.executable, str(STUBS / "echo_file.py")], timeout=30, env={"STUB_OUTPUT_FILE": str(payload)})
    assert (result.returncode, result.stdout) == (0, "hello")


def test_run_command_missing_binary_raises_producer_error() -> None:
    with pytest.raises(ProducerError, match="not found"):
        run_command(["definitely-not-a-binary-xyz"], timeout=5)


def test_run_command_timeout_raises_producer_error() -> None:
    with pytest.raises(ProducerError, match="timed out"):
        run_command([sys.executable, "-c", "import time; time.sleep(5)"], timeout=0.2)
```

Note `run_command` takes an optional `env` mapping that is merged over `os.environ`; add it to the Produces interface.

- [ ] **Step 2: Run the tests to see them fail**

Run: `uv run pytest tests/findings/test_adapters_common.py -q`
Expected: `ModuleNotFoundError`.

- [ ] **Step 3: Write producers.py and adapters/__init__.py**

`src/inspect_audit/findings/producers.py`:

```python
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
```

`src/inspect_audit/findings/adapters/__init__.py`:

```python
"""What every adapter shares: the run context, subject derivation, and a command runner that reports rather than raises."""

from __future__ import annotations

import os
import re
import subprocess
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any

import yaml

from ..models import Outcome, Revision, Run, Subject, TaskVersion, utcnow
from ..producers import ProducerConfig


@dataclass
class Context:
    """Everything an adapter needs beyond the target name."""

    ie_root: Path
    logs: list[Path] = field(default_factory=list)
    out_dir: Path = Path("findings-out")
    producers: ProducerConfig = field(default_factory=ProducerConfig)
    resolve: bool = False


class ProducerError(Exception):
    """A producer could not be run to completion."""


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    stdout: str
    stderr: str


def run_command(
    argv: Sequence[str],
    *,
    timeout: float,
    cwd: Path | None = None,
    env: Mapping[str, str] | None = None,
) -> CommandResult:
    """Run a producer and capture its output. Raises ProducerError for a missing binary or a timeout."""
    merged = {**os.environ, **(env or {})}
    try:
        completed = subprocess.run(
            list(argv), capture_output=True, text=True, timeout=timeout, cwd=cwd, env=merged, check=False
        )
    except FileNotFoundError as ex:
        raise ProducerError(f"producer binary not found: {argv[0]}") from ex
    except subprocess.TimeoutExpired as ex:
        raise ProducerError(f"producer timed out after {timeout:g}s: {' '.join(argv[:3])}") from ex
    return CommandResult(completed.returncode, completed.stdout, completed.stderr)


def package_of(target: str) -> str:
    """`inspect_evals/stereoset` -> `stereoset`."""
    return target.rsplit("/", 1)[-1]


def slug(text: str) -> str:
    return re.sub(r"-+", "-", re.sub(r"[^a-z0-9-]", "-", text.lower())).strip("-")


def eval_yaml(ie_root: Path, target: str) -> dict[str, Any]:
    """The eval's `eval.yaml` as a dict, or `{}` when absent or unreadable."""
    path = ie_root / "src" / "inspect_evals" / package_of(target) / "eval.yaml"
    try:
        loaded = yaml.safe_load(path.read_text())
    except (OSError, yaml.YAMLError):
        return {}
    return loaded if isinstance(loaded, dict) else {}


def task_version_from_yaml(data: Mapping[str, Any]) -> TaskVersion | None:
    raw = data.get("version")
    return TaskVersion.parse(str(raw)) if raw is not None else None


def _git(ie_root: Path, *args: str) -> str | None:
    try:
        completed = subprocess.run(
            ["git", "-C", str(ie_root), *args], capture_output=True, text=True, timeout=30, check=False
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return completed.stdout.strip() if completed.returncode == 0 else None


def repo_revision(ie_root: Path) -> Revision:
    """Commit and dirtiness from git; installed inspect_evals version as the fallback identity."""
    commit = _git(ie_root, "rev-parse", "HEAD")
    status = _git(ie_root, "status", "--porcelain")
    try:
        package_version: str | None = version("inspect_evals")
    except PackageNotFoundError:
        package_version = None
    if commit is None and package_version is None:
        package_version = "unknown"
    return Revision(commit=commit, package_version=package_version, dirty=None if status is None else bool(status))


def subject_for(target: str, ctx: Context) -> Subject:
    return Subject(
        eval=target,
        revision=repo_revision(ctx.ie_root),
        task_version=task_version_from_yaml(eval_yaml(ctx.ie_root, target)),
    )


def new_run_id(producer: str, target: str, timestamp: datetime) -> str:
    return f"{producer}-{timestamp.strftime('%Y%m%dT%H%M%SZ')}-{slug(target)}"


def skip_run(
    producer: str, target: str, ctx: Context, message: str, *, timestamp: datetime | None = None
) -> Run:
    """A run that could not happen: one skip outcome carrying the reason, no findings."""
    stamp = timestamp or utcnow()
    return Run(
        id=new_run_id(producer, target, stamp),
        timestamp=stamp,
        producer=producer,
        subject=subject_for(target, ctx),
        outcomes=[Outcome(rule=producer, status="skip", message=message[-2000:])],
    )
```

`slug` here matches the spec's `run.id` format. `new_run_id` uses the timestamp in UTC; callers pass `utcnow()`.

- [ ] **Step 4: Run the tests and the gate**

Run: `uv run pytest tests/findings -q && make check`
Expected: PASS and clean. If mypy flags `yaml` as untyped, `types-PyYAML` is already in the dev group; run `uv sync --python 3.13 --extra dev` again.

- [ ] **Step 5: Commit**

```bash
git add src/inspect_audit/findings tests/findings
git commit -m "$(cat <<'EOF'
Add producer configuration and shared adapter helpers

Command prefixes default to pinned uvx specs with environment overrides.
Adapters share subject derivation from git and eval.yaml, a never-raise
command runner and a skip-run constructor.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
EOF
)"
```

---

### Task 5: Lint adapter

**Files:**
- Create: `src/inspect_audit/findings/adapters/lint.py`
- Create: `tests/findings/fixtures/lint.json` (copy of `agent_artefacts/deterministic_pass/stereoset/lint.json`)
- Create: `tests/findings/fixtures/lint_two_packages.json`
- Create: `tests/findings/test_lint_adapter.py`

**Interfaces:**
- Consumes: Task 4 helpers, Task 1 models, `fingerprint`.
- Produces: `PRODUCER = "inspect_evals_lint"`, `LINT_RULES: dict[str, tuple[Dimension, Severity]]`, `parse(data: Mapping[str, Any], target: str, subject: Subject, *, timestamp: datetime, duration_s: float | None = None) -> Run`, `run(target: str, ctx: Context) -> Run`.

- [ ] **Step 1: Copy the fixture and write a two-package variant**

```bash
mkdir -p tests/findings/fixtures
cp /Users/matt/Developer/inspect_ai/inspect_audit/agent_artefacts/deterministic_pass/stereoset/lint.json tests/findings/fixtures/lint.json
uv run python - <<'EOF'
import json
from pathlib import Path
d = json.loads(Path("tests/findings/fixtures/lint.json").read_text())
other = json.loads(json.dumps(d["packages"][0]))
other["name"] = "hle"
other["diagnostics"] = []
other["passed"] = True
d["packages"].append(other)
Path("tests/findings/fixtures/lint_two_packages.json").write_text(json.dumps(d, indent=1))
EOF
```

- [ ] **Step 2: Write the failing tests**

`tests/findings/test_lint_adapter.py`:

```python
"""inspect-evals-lint JSON -> Run."""

import json
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

from inspect_audit.findings.adapters import Context
from inspect_audit.findings.adapters.lint import LINT_RULES, PRODUCER, parse, run
from inspect_audit.findings.models import CodeLocation
from inspect_audit.findings.producers import ProducerConfig
from test_adapters_common import STUBS, make_root

FIXTURES = Path(__file__).parent / "fixtures"
STAMP = datetime(2026, 9, 25, 4, 20, 50, tzinfo=UTC)


def _subject(tmp_path: Path):  # type: ignore[no-untyped-def]
    from inspect_audit.findings.adapters import subject_for

    root = make_root(tmp_path)
    return root, subject_for("inspect_evals/stereoset", Context(ie_root=root))


def test_parse_outcomes_and_the_one_finding(tmp_path: Path) -> None:
    _, subject = _subject(tmp_path)
    data = json.loads((FIXTURES / "lint.json").read_text())
    result = parse(data, "inspect_evals/stereoset", subject, timestamp=STAMP)
    assert result.producer == PRODUCER
    assert result.producer_version == "0.7.0"
    assert len(result.outcomes) == 25
    assert [o.status for o in result.outcomes].count("pass") == 18
    assert [o.status for o in result.outcomes].count("skip") == 7
    assert len(result.findings) == 1
    finding = result.findings[0]
    assert finding.rule == "IEBP008"
    assert finding.dimension == "dataset"
    assert finding.severity == "minor"
    assert finding.status == "supported"
    primary = finding.primary_location
    assert isinstance(primary, CodeLocation)
    assert (primary.file, primary.line, primary.column) == ("src/inspect_evals/stereoset/stereoset.py", 64, 15)
    assert finding.source.format == "inspect_evals_lint.Diagnostic@0.7.0"
    assert finding.source.record == data["packages"][0]["diagnostics"][0]
    assert finding.fingerprint.startswith("sha256:")
    assert finding.run_id == result.id


def test_parse_refuses_a_document_with_the_wrong_package(tmp_path: Path) -> None:
    _, subject = _subject(tmp_path)
    data = json.loads((FIXTURES / "lint_two_packages.json").read_text())
    result = parse(data, "inspect_evals/hle", subject.model_copy(update={"eval": "inspect_evals/hle"}), timestamp=STAMP)
    assert result.findings == []
    assert len(result.outcomes) == 25  # hle's rows only, not stereoset's


def test_parse_with_no_matching_package_is_a_skip(tmp_path: Path) -> None:
    _, subject = _subject(tmp_path)
    data = json.loads((FIXTURES / "lint.json").read_text())
    result = parse(data, "inspect_evals/gaia", subject.model_copy(update={"eval": "inspect_evals/gaia"}), timestamp=STAMP)
    assert [(o.status, o.rule) for o in result.outcomes] == [("skip", PRODUCER)]
    assert result.outcomes[0].message is not None and "gaia" in result.outcomes[0].message


def test_rule_table_defaults_and_overrides() -> None:
    assert LINT_RULES["IEBP008"] == ("dataset", "minor")
    assert LINT_RULES["IEBP005"] == ("environment", "minor")
    assert LINT_RULES.get("IEFS001", ("harness", "minor")) == ("harness", "minor")


def test_run_with_a_stubbed_producer(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root, _ = _subject(tmp_path)
    monkeypatch.setenv("STUB_OUTPUT_FILE", str(FIXTURES / "lint.json"))
    ctx = Context(ie_root=root, producers=ProducerConfig(lint=(sys.executable, str(STUBS / "echo_file.py"))))
    result = run("inspect_evals/stereoset", ctx)
    assert len(result.findings) == 1
    assert result.duration_s is not None and result.duration_s >= 0
    assert result.inputs["argv"][-5:] == ["--root", str(root), "stereoset", "--output-format", "json"]


def test_run_with_a_failing_producer_is_a_skip(tmp_path: Path) -> None:
    root, _ = _subject(tmp_path)
    ctx = Context(ie_root=root, producers=ProducerConfig(lint=(sys.executable, str(STUBS / "fail.py"))))
    result = run("inspect_evals/stereoset", ctx)
    assert result.findings == []
    assert result.outcomes[0].status == "skip"
    assert "boom" in (result.outcomes[0].message or "")


def test_run_with_exit_code_two_and_valid_json_is_still_a_skip(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root, _ = _subject(tmp_path)
    monkeypatch.setenv("STUB_OUTPUT_FILE", str(FIXTURES / "lint.json"))
    monkeypatch.setenv("STUB_EXIT", "2")
    ctx = Context(ie_root=root, producers=ProducerConfig(lint=(sys.executable, str(STUBS / "echo_file.py"))))
    result = run("inspect_evals/stereoset", ctx)
    assert result.outcomes[0].status == "skip"
    assert "exit 2" in (result.outcomes[0].message or "")


def test_run_with_exit_code_one_and_valid_json_parses(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # lint exits 1 when any check fails; that is a result, not an error
    root, _ = _subject(tmp_path)
    monkeypatch.setenv("STUB_OUTPUT_FILE", str(FIXTURES / "lint.json"))
    monkeypatch.setenv("STUB_EXIT", "1")
    ctx = Context(ie_root=root, producers=ProducerConfig(lint=(sys.executable, str(STUBS / "echo_file.py"))))
    result = run("inspect_evals/stereoset", ctx)
    assert len(result.findings) == 1
```

- [ ] **Step 3: Run the tests to see them fail**

Run: `uv run pytest tests/findings/test_lint_adapter.py -q`
Expected: `ModuleNotFoundError: No module named 'inspect_audit.findings.adapters.lint'`.

- [ ] **Step 4: Write the adapter**

`src/inspect_audit/findings/adapters/lint.py`:

```python
"""inspect-evals-lint: every outcome recorded, every diagnostic a finding with the row kept verbatim."""

from __future__ import annotations

import json
import time
from collections.abc import Mapping
from datetime import datetime
from typing import Any, Literal

from ..fingerprint import FINGERPRINT_VERSION, fingerprint
from ..models import CodeLocation, Dimension, Finding, Outcome, Run, Severity, Source, Subject, utcnow
from . import Context, ProducerError, new_run_id, package_of, run_command, skip_run, subject_for

PRODUCER = "inspect_evals_lint"

# dimension and severity per rule code. Default is ("harness", "minor"); this is the
# table to argue about, and it is one dict.
LINT_RULES: dict[str, tuple[Dimension, Severity]] = {
    "IEBP001": ("grading", "minor"),
    "IEBP002": ("grading", "minor"),
    "IEBP005": ("environment", "minor"),
    "IEBP006": ("environment", "minor"),
    "IEBP007": ("environment", "minor"),
    "IEBP008": ("dataset", "minor"),
    "IEBP009": ("dataset", "minor"),
}
_DEFAULT: tuple[Dimension, Severity] = ("harness", "minor")

def _status(raw: str) -> Literal["pass", "fail", "skip"]:
    """Lint statuses to ours: pass and skip carry over, suppressed is a skip, fail and warn are fails."""
    if raw == "pass":
        return "pass"
    if raw in ("skip", "suppressed"):
        return "skip"
    return "fail"


def parse(
    data: Mapping[str, Any],
    target: str,
    subject: Subject,
    *,
    timestamp: datetime,
    duration_s: float | None = None,
    inputs: Mapping[str, Any] | None = None,
) -> Run:
    """The lint JSON document for `target`'s package, as a Run."""
    run_id = new_run_id(PRODUCER, target, timestamp)
    version = str(data.get("version") or "unknown")
    package = package_of(target)
    packages = [p for p in data.get("packages", []) if p.get("name") == package]
    if not packages:
        found = ", ".join(str(p.get("name")) for p in data.get("packages", [])) or "(none)"
        return Run(
            id=run_id, timestamp=timestamp, producer=PRODUCER, producer_version=version, subject=subject,
            duration_s=duration_s, inputs=dict(inputs or {}),
            outcomes=[Outcome(rule=PRODUCER, status="skip", message=f"lint output has no package {package!r}; found {found}")],
        )
    entry = packages[0]
    outcomes = [
        Outcome(
            rule=str(row.get("code") or row.get("rule")),
            status=_status(str(row.get("status"))),
            message=row.get("message"),
        )
        for row in entry.get("outcomes", [])
    ]
    findings: list[Finding] = []
    for row in entry.get("diagnostics", []):
        code = str(row.get("code") or row.get("rule"))
        dimension, severity = LINT_RULES.get(code, _DEFAULT)
        primary = CodeLocation(
            role="primary", file=str(row.get("file")), line=row.get("line"), end_line=row.get("end_line"), column=row.get("column")
        )
        findings.append(
            Finding(
                fingerprint=fingerprint(PRODUCER, code, target, primary),
                fingerprint_version=FINGERPRINT_VERSION,
                producer=PRODUCER,
                rule=code,
                subject=subject,
                dimension=dimension,
                severity=severity,
                status="supported",
                summary=str(row.get("message") or code),
                locations=[primary],
                run_id=run_id,
                source=Source(format=f"inspect_evals_lint.Diagnostic@{version}", record=dict(row)),
            )
        )
    return Run(
        id=run_id, timestamp=timestamp, producer=PRODUCER, producer_version=version, subject=subject,
        duration_s=duration_s, inputs=dict(inputs or {}), outcomes=outcomes, findings=findings,
    )


def run(target: str, ctx: Context) -> Run:
    """Invoke lint for `target`'s package and parse it. Exit 1 means findings; anything else is a skip."""
    timestamp = utcnow()
    argv = [*ctx.producers.lint, "--root", str(ctx.ie_root), package_of(target), "--output-format", "json"]
    started = time.monotonic()
    try:
        result = run_command(argv, timeout=ctx.producers.timeout_s)
    except ProducerError as ex:
        return skip_run(PRODUCER, target, ctx, str(ex), timestamp=timestamp)
    duration = time.monotonic() - started
    if result.returncode not in (0, 1):
        return skip_run(PRODUCER, target, ctx, f"lint exit {result.returncode}: {result.stderr or result.stdout}", timestamp=timestamp)
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError as ex:
        return skip_run(PRODUCER, target, ctx, f"lint output is not JSON: {ex}; stderr: {result.stderr[-500:]}", timestamp=timestamp)
    return parse(
        data, target, subject_for(target, ctx), timestamp=timestamp, duration_s=duration, inputs={"argv": argv}
    )
```

- [ ] **Step 5: Run the tests and the gate**

Run: `uv run pytest tests/findings -q && make check`
Expected: PASS and clean.

- [ ] **Step 6: Commit**

```bash
git add src/inspect_audit/findings/adapters/lint.py tests/findings/fixtures/lint*.json tests/findings/test_lint_adapter.py
git commit -m "$(cat <<'EOF'
Add the inspect-evals-lint adapter

Exit 1 is lint's "checks failed" and parses; other exits are skips.
Dimension and severity come from a one-dict rule table.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
EOF
)"
```

---

### Task 6: Dataset adapter

**Files:**
- Create: `src/inspect_audit/findings/adapters/dataset.py`
- Create: `tests/findings/fixtures/dataset/scan_summary.json`, `duplicate_questions.json`, `inconsistent_format.json` (copies; not `answer_length.json`)
- Create: `tests/findings/test_dataset_adapter.py`

**Interfaces:**
- Consumes: Task 4 helpers, Task 1 models.
- Produces: `PRODUCER = "inspect_dataset"`, `DATASET_OVERRIDES: dict[str, dict[str, str]]`, `SEVERITY: dict[str, Severity]`, `hf_asset(data: Mapping[str, Any]) -> str | None`, `parse(scan_dir: Path, target: str, subject: Subject, *, timestamp: datetime, duration_s: float | None = None, inputs: Mapping[str, Any] | None = None) -> Run`, `run(target: str, ctx: Context) -> Run`.

- [ ] **Step 1: Copy the fixtures**

```bash
mkdir -p tests/findings/fixtures/dataset
S=/Users/matt/Developer/inspect_ai/inspect_audit/agent_artefacts/deterministic_pass/stereoset/dataset
cp $S/scan_summary.json $S/duplicate_questions.json $S/inconsistent_format.json tests/findings/fixtures/dataset/
```

- [ ] **Step 2: Write the failing tests**

`tests/findings/test_dataset_adapter.py`:

```python
"""inspect-dataset scan output -> Run."""

import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

from inspect_audit.findings.adapters import Context, subject_for
from inspect_audit.findings.adapters.dataset import DATASET_OVERRIDES, PRODUCER, hf_asset, parse, run
from inspect_audit.findings.models import SampleLocation
from inspect_audit.findings.producers import ProducerConfig
from test_adapters_common import STUBS, make_root

FIXTURE = Path(__file__).parent / "fixtures" / "dataset"
STAMP = datetime(2026, 9, 25, 4, 20, 50, tzinfo=UTC)
ASSET = "external_assets:\n  - type: huggingface\n    source: McGill-NLP/stereoset\n    fetch_method: hf_dataset\n    state: pinned\n"


def test_parse_summary_outcomes_and_findings(tmp_path: Path) -> None:
    root = make_root(tmp_path, extra=ASSET)
    subject = subject_for("inspect_evals/stereoset", Context(ie_root=root))
    result = parse(FIXTURE, "inspect_evals/stereoset", subject, timestamp=STAMP)
    assert result.producer == PRODUCER
    assert result.subject.dataset is not None
    assert (result.subject.dataset.path, result.subject.dataset.config, result.subject.dataset.split) == (
        "McGill-NLP/stereoset", "intersentence", "validation")
    assert result.subject.dataset.revision is None
    assert sorted((o.rule, o.status) for o in result.outcomes) == [
        ("answer_length", "fail"), ("duplicate_questions", "fail"), ("inconsistent_format", "fail")]
    # answer_length.json is absent from the fixture on size grounds; its 2,123 rows are counted in the
    # outcome but produce no findings here
    assert len(result.findings) == 18 + 28
    dup = next(f for f in result.findings if f.rule == "duplicate_questions")
    assert dup.dimension == "dataset" and dup.severity == "none"
    primary = dup.primary_location
    assert isinstance(primary, SampleLocation)
    assert primary.dataset == "McGill-NLP/stereoset"
    assert primary.sample_id == "2a994bc105c63ddfdd912f52cbf8c63a"
    assert dup.source.format.startswith("inspect_dataset.Finding@")
    assert dup.source.record["explanation"].startswith("Question appears 2 times")  # type: ignore[index]
    fmt = next(f for f in result.findings if f.rule == "inconsistent_format")
    assert fmt.severity == "minor"
    assert len(fmt.summary) <= 200


def test_hf_asset_reads_eval_yaml() -> None:
    assert hf_asset({"external_assets": [{"type": "huggingface", "source": "a/b"}]}) == "a/b"
    assert hf_asset({"external_assets": [{"type": "url", "source": "https://x"}]}) is None
    assert hf_asset({}) is None


def test_overrides_include_stereoset() -> None:
    assert DATASET_OVERRIDES["inspect_evals/stereoset"] == {
        "config": "intersentence", "split": "validation",
        "question_field": "context", "answer_field": "sentences", "id_field": "id"}


def test_run_without_an_asset_is_a_skip(tmp_path: Path) -> None:
    root = make_root(tmp_path)
    result = run("inspect_evals/stereoset", Context(ie_root=root))
    assert result.outcomes[0].status == "skip"
    assert "no huggingface asset" in (result.outcomes[0].message or "")


def test_run_with_a_stubbed_scan(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = make_root(tmp_path, extra=ASSET)
    monkeypatch.setenv("STUB_OUTPUT_DIR", str(FIXTURE))
    ctx = Context(ie_root=root, out_dir=tmp_path / "out", producers=ProducerConfig(dataset=(sys.executable, str(STUBS / "echo_file.py"))))
    result = run("inspect_evals/stereoset", ctx)
    assert len(result.findings) == 46
    argv = result.inputs["argv"]
    assert isinstance(argv, list)
    assert "McGill-NLP/stereoset" in argv and "--config" in argv and "--question-field" in argv


def test_run_with_a_failing_scan_is_a_skip(tmp_path: Path) -> None:
    root = make_root(tmp_path, extra=ASSET)
    ctx = Context(ie_root=root, out_dir=tmp_path / "out", producers=ProducerConfig(dataset=(sys.executable, str(STUBS / "fail.py"))))
    result = run("inspect_evals/stereoset", ctx)
    assert result.outcomes[0].status == "skip"
    assert "boom" in (result.outcomes[0].message or "")
```

- [ ] **Step 3: Run the tests to see them fail**

Run: `uv run pytest tests/findings/test_dataset_adapter.py -q`
Expected: `ModuleNotFoundError`.

- [ ] **Step 4: Write the adapter**

`src/inspect_audit/findings/adapters/dataset.py`:

```python
"""inspect-dataset: static scanners over the eval's HuggingFace dataset, one finding per row."""

from __future__ import annotations

import json
import tempfile
import time
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path
from typing import Any

from ..fingerprint import FINGERPRINT_VERSION, fingerprint
from ..models import DatasetRef, Finding, Outcome, Run, SampleLocation, Severity, Source, Subject, utcnow
from . import Context, ProducerError, eval_yaml, new_run_id, run_command, skip_run, subject_for

PRODUCER = "inspect_dataset"

# per-eval scan arguments where auto-detection does not work. Keys are CLI option names without dashes.
DATASET_OVERRIDES: dict[str, dict[str, str]] = {
    "inspect_evals/stereoset": {
        "config": "intersentence",
        "split": "validation",
        "question_field": "context",
        "answer_field": "sentences",
        "id_field": "id",
    },
}

SEVERITY: dict[str, Severity] = {"low": "none", "medium": "minor", "high": "major"}
_SUMMARY_CHARS = 200


def hf_asset(data: Mapping[str, Any]) -> str | None:
    """The first HuggingFace source named in eval.yaml's external_assets, if any."""
    for asset in data.get("external_assets", []) or []:
        if isinstance(asset, dict) and asset.get("type") == "huggingface" and asset.get("source"):
            return str(asset["source"])
    return None


def _rows(path: Path) -> list[dict[str, Any]]:
    loaded = json.loads(path.read_text())
    rows = loaded.get("findings", []) if isinstance(loaded, dict) else loaded
    return [row for row in rows if isinstance(row, dict)]


def parse(
    scan_dir: Path,
    target: str,
    subject: Subject,
    *,
    timestamp: datetime,
    duration_s: float | None = None,
    inputs: Mapping[str, Any] | None = None,
) -> Run:
    """An inspect-dataset output directory as a Run. Scanners named in the summary without a file still get an outcome."""
    run_id = new_run_id(PRODUCER, target, timestamp)
    summary = json.loads((scan_dir / "scan_summary.json").read_text())
    version = str(summary.get("version") or "unknown")
    dataset = DatasetRef(
        path=summary.get("dataset_name"), config=summary.get("config"), split=summary.get("split"), revision=summary.get("revision")
    )
    subject = subject.model_copy(update={"dataset": dataset})
    outcomes: list[Outcome] = []
    findings: list[Finding] = []
    for scanner, counts in sorted((summary.get("by_scanner") or {}).items()):
        total = int((counts or {}).get("total", 0))
        outcomes.append(Outcome(rule=scanner, status="fail" if total else "pass", message=f"{total} finding(s)"))
        path = scan_dir / f"{scanner}.json"
        if not path.is_file():
            continue
        for row in _rows(path):
            sample_id = str(row.get("sample_id") if row.get("sample_id") is not None else row.get("sample_index"))
            primary = SampleLocation(role="primary", dataset=dataset.path or "", sample_id=sample_id)
            explanation = str(row.get("explanation") or scanner)
            findings.append(
                Finding(
                    fingerprint=fingerprint(PRODUCER, scanner, target, primary),
                    fingerprint_version=FINGERPRINT_VERSION,
                    producer=PRODUCER,
                    rule=scanner,
                    subject=subject,
                    dimension="dataset",
                    severity=SEVERITY.get(str(row.get("severity")), "minor"),
                    status="supported",
                    summary=explanation[:_SUMMARY_CHARS],
                    locations=[primary],
                    run_id=run_id,
                    source=Source(format=f"inspect_dataset.Finding@{version}", record=dict(row)),
                )
            )
    return Run(
        id=run_id, timestamp=timestamp, producer=PRODUCER, producer_version=version, subject=subject,
        duration_s=duration_s, inputs=dict(inputs or {}), outcomes=outcomes, findings=findings,
    )


def run(target: str, ctx: Context) -> Run:
    """Scan the eval's HuggingFace dataset with static scanners into a temporary directory, then parse it."""
    timestamp = utcnow()
    source = hf_asset(eval_yaml(ctx.ie_root, target))
    if source is None:
        return skip_run(PRODUCER, target, ctx, "no huggingface asset in eval.yaml external_assets", timestamp=timestamp)
    overrides = DATASET_OVERRIDES.get(target, {})
    ctx.out_dir.mkdir(parents=True, exist_ok=True)
    scan_dir = Path(tempfile.mkdtemp(prefix="inspect_dataset_", dir=ctx.out_dir))
    argv = [*ctx.producers.dataset, "scan", source]
    for key, value in overrides.items():
        argv += [f"--{key.replace('_', '-')}", value]
    argv += ["-o", str(scan_dir)]
    started = time.monotonic()
    try:
        result = run_command(argv, timeout=ctx.producers.timeout_s)
    except ProducerError as ex:
        return skip_run(PRODUCER, target, ctx, str(ex), timestamp=timestamp)
    duration = time.monotonic() - started
    if result.returncode != 0 or not (scan_dir / "scan_summary.json").is_file():
        return skip_run(
            PRODUCER, target, ctx,
            f"inspect-dataset exit {result.returncode}: {(result.stderr or result.stdout)[-1500:]}", timestamp=timestamp,
        )
    return parse(scan_dir, target, subject_for(target, ctx), timestamp=timestamp, duration_s=duration, inputs={"argv": argv, "scan_dir": str(scan_dir)})
```

- [ ] **Step 5: Run the tests and the gate**

Run: `uv run pytest tests/findings -q && make check`
Expected: PASS and clean.

- [ ] **Step 6: Commit**

```bash
git add src/inspect_audit/findings/adapters/dataset.py tests/findings/fixtures/dataset tests/findings/test_dataset_adapter.py
git commit -m "$(cat <<'EOF'
Add the inspect-dataset adapter

The HuggingFace source comes from eval.yaml; scan fields come from a
per-eval override table because auto-detection fails on StereoSet.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
EOF
)"
```

---

### Task 7: Header adapter

**Files:**
- Create: `src/inspect_audit/findings/adapters/header.py`
- Create: `tests/findings/test_header_adapter.py`

**Interfaces:**
- Consumes: Task 4 helpers, Task 1 models, `inspect_ai.log.read_eval_log`, `test_helpers.logs.fixture_task`.
- Produces: `PRODUCER = "inspect_audit_header"`, `matching_headers(logs: Sequence[Path], target: str) -> list[tuple[Path, EvalLog]]`, `parse(headers: Sequence[tuple[Path, EvalLog]], target: str, subject: Subject, yaml_data: Mapping[str, Any], *, timestamp: datetime, resolved_ids: set[str] | None = None) -> Run`, `run(target: str, ctx: Context) -> Run`, `eval_spec_dict(log: EvalLog) -> dict[str, JsonValue]`.

- [ ] **Step 1: Write the failing tests**

`tests/findings/test_header_adapter.py`:

```python
"""Log headers -> Run: sample counts, version drift, unscored samples, dirty revisions, unknown ids."""

from datetime import UTC, datetime
from pathlib import Path

from inspect_ai import Task, eval
from inspect_ai.dataset import MemoryDataset, Sample
from inspect_ai.log import read_eval_log
from inspect_ai.scorer import match

from inspect_audit.findings.adapters import Context, subject_for
from inspect_audit.findings.adapters.header import PRODUCER, eval_spec_dict, matching_headers, parse, run
from inspect_audit.findings.models import LogLocation, ScorerLocation
from test_adapters_common import make_root

STAMP = datetime(2026, 9, 25, 4, 20, 50, tzinfo=UTC)


def _log(log_dir: Path, *, name: str = "stereoset", version: int | str = 3, samples: int = 3) -> Path:
    task = Task(
        name=name,
        dataset=MemoryDataset([Sample(id=i, input=f"q{i}", target="ANSWER") for i in range(1, samples + 1)]),
        scorer=match(),
        version=version,
    )
    return Path(eval(task, model="mockllm/model", log_dir=str(log_dir), display="none")[0].location.removeprefix("file://"))


def test_matching_on_registry_name_or_task(tmp_path: Path) -> None:
    logs = [_log(tmp_path / "a"), _log(tmp_path / "b", name="hle")]
    matched = matching_headers(logs, "inspect_evals/stereoset")
    assert [path.name for path, _ in matched] == [logs[0].name]
    # an inline Task has no registry name, so this match came from eval.task
    assert read_eval_log(str(logs[0]), header_only=True).eval.task_registry_name in (None, "stereoset")


def test_dataset_samples_mismatch_fires(tmp_path: Path) -> None:
    root = make_root(tmp_path)  # eval.yaml says 2123
    log = _log(tmp_path / "logs", samples=3)
    ctx = Context(ie_root=root, logs=[log])
    result = run("inspect_evals/stereoset", ctx)
    assert result.producer == PRODUCER
    finding = next(f for f in result.findings if f.rule == "header.dataset_samples")
    assert finding.dimension == "dataset" and finding.severity == "minor"
    primary = finding.primary_location
    assert isinstance(primary, LogLocation) and primary.path == "eval.dataset.samples"
    assert finding.source.eval_spec is not None
    assert "sample_ids" not in finding.source.eval_spec["dataset"]  # type: ignore[operator]
    assert "3" in finding.summary and "2123" in finding.summary
    assert any(o.rule == "header.dataset_samples" and o.status == "fail" for o in result.outcomes)


def test_dataset_samples_uses_the_matching_task_entry(tmp_path: Path) -> None:
    extra = "  - name: other_task\n    dataset_samples: 3\n"
    root = make_root(tmp_path, extra=extra)  # stereoset: 2123, other_task: 3
    log = _log(tmp_path / "logs", samples=3)
    result = run("inspect_evals/stereoset", Context(ie_root=root, logs=[log]))
    assert any(f.rule == "header.dataset_samples" for f in result.findings)


def test_version_drift_across_logs(tmp_path: Path) -> None:
    root = make_root(tmp_path)
    logs = [_log(tmp_path / "a", version=3), _log(tmp_path / "b", version=4)]
    result = run("inspect_evals/stereoset", Context(ie_root=root, logs=logs))
    drift = [f for f in result.findings if f.rule == "header.version_drift"]
    assert len(drift) == 1
    assert drift[0].dimension == "informativeness"
    assert len(drift[0].locations) == 2
    assert sum(1 for loc in drift[0].locations if loc.role == "primary") == 1


def test_no_drift_with_one_version(tmp_path: Path) -> None:
    root = make_root(tmp_path)
    logs = [_log(tmp_path / "a"), _log(tmp_path / "b")]
    result = run("inspect_evals/stereoset", Context(ie_root=root, logs=logs))
    assert not any(f.rule == "header.version_drift" for f in result.findings)
    assert any(o.rule == "header.version_drift" and o.status == "pass" for o in result.outcomes)


def test_no_logs_is_a_skip(tmp_path: Path) -> None:
    root = make_root(tmp_path)
    result = run("inspect_evals/stereoset", Context(ie_root=root, logs=[_log(tmp_path / "x", name="hle")]))
    assert [o.status for o in result.outcomes] == ["skip"]
    assert "no logs" in (result.outcomes[0].message or "")


def test_unscored_and_dirty_checks_exist(tmp_path: Path) -> None:
    root = make_root(tmp_path)
    log = _log(tmp_path / "logs")
    result = run("inspect_evals/stereoset", Context(ie_root=root, logs=[log]))
    rules = {o.rule for o in result.outcomes}
    assert {"header.dataset_samples", "header.version_drift", "header.unscored_samples", "header.dirty_revision"} <= rules
    assert "header.unknown_sample_ids" not in rules  # --resolve off


def test_unknown_sample_ids_with_resolved_ids(tmp_path: Path) -> None:
    root = make_root(tmp_path)
    log = _log(tmp_path / "logs", samples=3)
    header = read_eval_log(str(log), header_only=True)
    subject = subject_for("inspect_evals/stereoset", Context(ie_root=root))
    result = parse([(log, header)], "inspect_evals/stereoset", subject, {}, timestamp=STAMP, resolved_ids={"1", "2"})
    finding = next(f for f in result.findings if f.rule == "header.unknown_sample_ids")
    assert finding.severity == "major"
    assert "1 of 3" in finding.summary


def test_eval_spec_dict_drops_sample_ids(tmp_path: Path) -> None:
    header = read_eval_log(str(_log(tmp_path / "logs")), header_only=True)
    spec = eval_spec_dict(header)
    assert "sample_ids" not in spec["dataset"]  # type: ignore[operator]
    assert spec["task"] == "stereoset"


def test_scorer_location_for_unscored(tmp_path: Path) -> None:
    # exercise the location type even though mockllm scores everything: build a finding via parse on a
    # header whose results are edited to report an unscored sample
    root = make_root(tmp_path)
    log = _log(tmp_path / "logs")
    header = read_eval_log(str(log), header_only=True)
    assert header.results is not None
    header.results.scores[0].unscored_samples = 1
    subject = subject_for("inspect_evals/stereoset", Context(ie_root=root))
    result = parse([(log, header)], "inspect_evals/stereoset", subject, {}, timestamp=STAMP)
    finding = next(f for f in result.findings if f.rule == "header.unscored_samples")
    assert isinstance(finding.primary_location, ScorerLocation)
    assert finding.primary_location.scorer == "match"
```

- [ ] **Step 2: Run the tests to see them fail**

Run: `uv run pytest tests/findings/test_header_adapter.py -q`
Expected: `ModuleNotFoundError`.

- [ ] **Step 3: Write the adapter**

`src/inspect_audit/findings/adapters/header.py`:

```python
"""Facts a log header settles without a model: sample counts, drift, unscored samples, dirty revisions, unknown ids."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any

from inspect_ai.log import EvalLog, read_eval_log
from pydantic import JsonValue

from ..fingerprint import FINGERPRINT_VERSION, fingerprint
from ..models import (
    AnyLocation,
    Dimension,
    Finding,
    LogLocation,
    Outcome,
    Run,
    ScorerLocation,
    Severity,
    Source,
    Subject,
    utcnow,
)
from . import Context, eval_yaml, new_run_id, package_of, skip_run, subject_for

PRODUCER = "inspect_audit_header"

_RULES: dict[str, tuple[Dimension, Severity]] = {
    "header.dataset_samples": ("dataset", "minor"),
    "header.version_drift": ("informativeness", "minor"),
    "header.unscored_samples": ("grading", "minor"),
    "header.dirty_revision": ("informativeness", "none"),
    "header.unknown_sample_ids": ("dataset", "major"),
}


def eval_spec_dict(log: EvalLog) -> dict[str, JsonValue]:
    """The header's `eval` block as JSON, minus the potentially huge sample id list."""
    spec: dict[str, Any] = log.eval.model_dump(mode="json", exclude_none=True)
    dataset = spec.get("dataset")
    if isinstance(dataset, dict):
        dataset.pop("sample_ids", None)
    return spec


def _tail(name: str | None) -> str | None:
    return name.rsplit("/", 1)[-1] if name else None


def matching_headers(logs: Sequence[Path], target: str) -> list[tuple[Path, EvalLog]]:
    """Headers whose registry name or task name matches `target` on the unqualified name."""
    wanted = package_of(target)
    matched: list[tuple[Path, EvalLog]] = []
    for path in logs:
        try:
            header = read_eval_log(str(path), header_only=True)
        except Exception:  # noqa: BLE001 - an unreadable log is not this eval's problem
            continue
        if wanted in {_tail(header.eval.task_registry_name), _tail(header.eval.task)}:
            matched.append((path, header))
    return matched


def _declared_samples(yaml_data: Mapping[str, Any], target: str) -> int | None:
    wanted = package_of(target)
    tasks = yaml_data.get("tasks")
    for entry in tasks if isinstance(tasks, list) else []:
        if isinstance(entry, dict) and entry.get("name") == wanted and isinstance(entry.get("dataset_samples"), int):
            return int(entry["dataset_samples"])
    return None


class _Builder:
    def __init__(self, target: str, subject: Subject, run_id: str) -> None:
        self.target, self.subject, self.run_id = target, subject, run_id
        self.outcomes: list[Outcome] = []
        self.findings: list[Finding] = []

    def outcome(self, rule: str, fired: bool, message: str | None = None) -> None:
        self.outcomes.append(Outcome(rule=rule, status="fail" if fired else "pass", message=message))

    def finding(self, rule: str, summary: str, locations: list[AnyLocation], header: EvalLog) -> None:
        dimension, severity = _RULES[rule]
        primary = next(location for location in locations if location.role == "primary")
        self.findings.append(
            Finding(
                fingerprint=fingerprint(PRODUCER, rule, self.target, primary),
                fingerprint_version=FINGERPRINT_VERSION,
                producer=PRODUCER,
                rule=rule,
                subject=self.subject,
                dimension=dimension,
                severity=severity,
                status="supported",
                summary=summary,
                locations=locations,
                run_id=self.run_id,
                source=Source(format="inspect_ai.log.EvalSpec", record=None, eval_spec=eval_spec_dict(header)),
            )
        )


def parse(
    headers: Sequence[tuple[Path, EvalLog]],
    target: str,
    subject: Subject,
    yaml_data: Mapping[str, Any],
    *,
    timestamp: datetime,
    resolved_ids: set[str] | None = None,
) -> Run:
    """Run the header checks over already-read headers."""
    run_id = new_run_id(PRODUCER, target, timestamp)
    build = _Builder(target, subject, run_id)

    declared = _declared_samples(yaml_data, target)
    fired = False
    for path, header in headers:
        actual = header.eval.dataset.samples
        if declared is not None and actual is not None and actual != declared:
            fired = True
            build.finding(
                "header.dataset_samples",
                f"log records {actual} dataset samples; eval.yaml declares {declared}",
                [LogLocation(role="primary", eval_id=header.eval.eval_id, path="eval.dataset.samples", location_hint=str(path), quote=str(actual))],
                header,
            )
    build.outcome("header.dataset_samples", fired, None if declared is not None else "eval.yaml declares no dataset_samples")

    versions = {(str(h.eval.task_version), (h.eval.packages or {}).get("inspect_evals")) for _, h in headers}
    if len(versions) > 1:
        newest = max(headers, key=lambda pair: pair[1].eval.created)
        locations: list[AnyLocation] = [
            LogLocation(role="primary" if header is newest[1] else "related", eval_id=header.eval.eval_id,
                        path="eval.task_version", location_hint=str(path),
                        quote=f"{header.eval.task_version} / {(header.eval.packages or {}).get('inspect_evals')}")
            for path, header in headers
        ]
        build.finding(
            "header.version_drift",
            f"{len(headers)} logs span {len(versions)} distinct task or package versions",
            locations, newest[1],
        )
    build.outcome("header.version_drift", len(versions) > 1)

    unscored_fired = False
    for path, header in headers:
        for score in (header.results.scores if header.results else []):
            if score.unscored_samples:
                unscored_fired = True
                build.finding(
                    "header.unscored_samples",
                    f"scorer {score.name} left {score.unscored_samples} of {header.results.total_samples if header.results else '?'} samples unscored",  # type: ignore[union-attr]
                    [ScorerLocation(role="primary", eval_id=header.eval.eval_id, scorer=score.name, quote=str(score.unscored_samples))],
                    header,
                )
    build.outcome("header.unscored_samples", unscored_fired)

    dirty_fired = False
    for path, header in headers:
        if header.eval.revision is not None and header.eval.revision.dirty:
            dirty_fired = True
            build.finding(
                "header.dirty_revision",
                f"log was produced from a dirty checkout of {header.eval.revision.origin} at {header.eval.revision.commit}",
                [LogLocation(role="primary", eval_id=header.eval.eval_id, path="eval.revision.dirty", location_hint=str(path), quote="true")],
                header,
            )
    build.outcome("header.dirty_revision", dirty_fired)

    if resolved_ids is not None:
        unknown_fired = False
        for path, header in headers:
            logged = [str(i) for i in (header.eval.dataset.sample_ids or [])]
            missing = [i for i in logged if i not in resolved_ids]
            if missing:
                unknown_fired = True
                build.finding(
                    "header.unknown_sample_ids",
                    f"{len(missing)} of {len(logged)} logged sample ids are not in the resolved dataset (e.g. {missing[0]})",
                    [LogLocation(role="primary", eval_id=header.eval.eval_id, path="eval.dataset.sample_ids", location_hint=str(path))],
                    header,
                )
        build.outcome("header.unknown_sample_ids", unknown_fired)

    return Run(
        id=run_id, timestamp=timestamp, producer=PRODUCER, subject=subject,
        inputs={"logs": [str(path) for path, _ in headers]}, outcomes=build.outcomes, findings=build.findings,
    )


def _resolved_ids(target: str, headers: Sequence[tuple[Path, EvalLog]]) -> set[str] | None:
    from inspect_audit._resolve import resolve_task

    task_args = dict(headers[0][1].eval.task_args or {}) if headers else {}
    try:
        task = resolve_task(target, task_args)
    except Exception:  # noqa: BLE001 - resolution failure is reported by the caller as a skip outcome
        return None
    return {str(sample.id) for sample in task.dataset}


def run(target: str, ctx: Context) -> Run:
    """Read the headers of the logs that match `target` and run the checks."""
    timestamp = utcnow()
    headers = matching_headers(ctx.logs, target)
    if not headers:
        return skip_run(PRODUCER, target, ctx, f"no logs for target {target} among {len(ctx.logs)} file(s)", timestamp=timestamp)
    resolved = _resolved_ids(target, headers) if ctx.resolve else None
    result = parse(headers, target, subject_for(target, ctx), eval_yaml(ctx.ie_root, target), timestamp=timestamp, resolved_ids=resolved)
    if ctx.resolve and resolved is None:
        result.outcomes.append(Outcome(rule="header.unknown_sample_ids", status="skip", message="could not resolve the task to compare sample ids"))
    return result
```

`BLE001` is not in the selected ruff rules, so drop the `noqa: BLE001` comments if ruff flags them as unused. `eval.created` is an ISO string, so `max(..., key=...)` on it orders by time.

- [ ] **Step 4: Run the tests and the gate**

Run: `uv run pytest tests/findings -q && make check`
Expected: PASS and clean. The `eval()` calls under `mockllm` take a second or two each.

- [ ] **Step 5: Commit**

```bash
git add src/inspect_audit/findings/adapters/header.py tests/findings/test_header_adapter.py
git commit -m "$(cat <<'EOF'
Add the log header adapter

Five zero-cost checks over .eval headers: dataset sample count against
eval.yaml, task and package version drift, unscored samples, dirty
revisions, and (behind --resolve) logged ids missing from the dataset.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
EOF
)"
```

---

### Task 8: Featured list, CLI and console script

**Files:**
- Create: `src/inspect_audit/findings/featured.py`
- Create: `src/inspect_audit/findings/cli.py`
- Create: `tests/findings/test_cli.py`
- Modify: `pyproject.toml` (add `[project.scripts]`)

**Interfaces:**
- Consumes: everything above; `inspect_audit._registry.fetch_logs(logs: str | list[str]) -> str`; `inspect_ai.log.list_eval_logs`.
- Produces: `FEATURED: tuple[str, ...]`, `main(argv: Sequence[str] | None = None) -> int`, `collect_logs(sources: Sequence[str]) -> list[Path]`, `sweep(targets, ctx, producers: set[str]) -> dict[str, list[Run]]`, `write_outputs(out: Path, runs_by_eval: Mapping[str, Sequence[Run]]) -> None`.

- [ ] **Step 1: Write the featured list**

`src/inspect_audit/findings/featured.py`:

```python
"""The 35 evaluations the inspect_evals docs catalogue marks Featured.

Copied from inspect_evals `docs/_templates/evals.ejs` (`featuredEvalIds`) on 2026-09-25. The list
lives nowhere structured upstream; that is recorded as a gap in the spec.
"""

FEATURED: tuple[str, ...] = (
    "cybench", "cybergym", "cve_bench", "mask", "hle", "ape",
    "agentharm", "scicode", "healthbench", "simpleqa", "agentdojo",
    "bigcodebench", "cti_realm", "gaia", "gdpval", "kernelbench", "usaco",
    "alignment_faking",
    "agentic_misalignment", "bfcl", "exploitbench", "frontier_cs", "gpqa",
    "lab_bench", "lab_bench_2", "lingoly", "mle_bench", "mlrc_bench",
    "paperbench", "mmlu_pro", "swe_lancer", "xstest", "strong_reject",
    "fortress", "bixbench",
)
```

- [ ] **Step 2: Write the failing CLI tests**

`tests/findings/test_cli.py`:

```python
"""End to end over a temporary inspect_evals root with stubbed producers."""

import sys
from pathlib import Path

import pandas as pd
import pytest

from inspect_audit.findings.cli import collect_logs, main
from inspect_audit.findings.featured import FEATURED
from test_adapters_common import STUBS, make_root
from test_header_adapter import _log

FIXTURES = Path(__file__).parent / "fixtures"
ASSET = "external_assets:\n  - type: huggingface\n    source: McGill-NLP/stereoset\n    fetch_method: hf_dataset\n    state: pinned\n"


def _stubbed_env(monkeypatch: pytest.MonkeyPatch, *, lint_ok: bool = True) -> None:
    lint = str(STUBS / ("echo_file.py" if lint_ok else "fail.py"))
    monkeypatch.setenv("INSPECT_AUDIT_LINT_CMD", f"{sys.executable} {lint}")
    monkeypatch.setenv("INSPECT_AUDIT_DATASET_CMD", f"{sys.executable} {STUBS / 'echo_file.py'}")
    monkeypatch.setenv("STUB_OUTPUT_FILE", str(FIXTURES / "lint.json"))
    monkeypatch.setenv("STUB_OUTPUT_DIR", str(FIXTURES / "dataset"))


def test_featured_has_35_ids() -> None:
    assert len(FEATURED) == 35 and len(set(FEATURED)) == 35 and "stereoset" not in FEATURED


def test_collect_logs_lists_directories_and_files(tmp_path: Path) -> None:
    a = _log(tmp_path / "a")
    b = _log(tmp_path / "b" / "nested")
    assert sorted(p.name for p in collect_logs([str(tmp_path / "a"), str(b)])) == sorted([a.name, b.name])


def test_run_writes_the_layout(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = make_root(tmp_path, extra=ASSET)
    log = _log(tmp_path / "logs", samples=3)
    _stubbed_env(monkeypatch)
    out = tmp_path / "out"
    code = main(["run", "--root", str(root), "--logs", str(log), "--out", str(out), "inspect_evals/stereoset"])
    assert code == 0
    slug_dir = out / "inspect-evals-stereoset"
    assert sorted(p.name for p in slug_dir.glob("*.run.json")) == ["dataset.run.json", "header.run.json", "lint.run.json"]
    assert (slug_dir / "SUMMARY.md").read_text().startswith("# inspect_evals/stereoset")
    assert (out / "SUMMARY.md").read_text().startswith("# Sweep summary")
    findings = pd.read_parquet(out / "findings.parquet")
    assert len(findings) == 1 + 46 + 1  # lint, dataset, header.dataset_samples (3 != 2123)
    runs = pd.read_parquet(out / "runs.parquet")
    assert len(runs) == 3 and not runs["skipped"].any()


def test_run_with_a_failing_producer_exits_one_and_records_a_skip(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = make_root(tmp_path, extra=ASSET)
    log = _log(tmp_path / "logs")
    _stubbed_env(monkeypatch, lint_ok=False)
    out = tmp_path / "out"
    code = main(["run", "--root", str(root), "--logs", str(log), "--out", str(out), "inspect_evals/stereoset"])
    assert code == 1
    runs = pd.read_parquet(out / "runs.parquet")
    assert runs.loc[runs["producer"] == "inspect_evals_lint", "skipped"].item()


def test_producers_flag_selects_external_producers_only(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = make_root(tmp_path, extra=ASSET)
    log = _log(tmp_path / "logs")
    _stubbed_env(monkeypatch)
    out = tmp_path / "out"
    assert main(["run", "--root", str(root), "--logs", str(log), "--out", str(out), "--producers", "lint", "inspect_evals/stereoset"]) == 0
    assert sorted(p.name for p in (out / "inspect-evals-stereoset").glob("*.run.json")) == ["header.run.json", "lint.run.json"]


def test_summary_regenerates_identical_files(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = make_root(tmp_path, extra=ASSET)
    log = _log(tmp_path / "logs")
    _stubbed_env(monkeypatch)
    out = tmp_path / "out"
    main(["run", "--root", str(root), "--logs", str(log), "--out", str(out), "inspect_evals/stereoset"])
    before = {p: p.read_text() for p in out.rglob("SUMMARY.md")}
    for path in before:
        path.write_text("stale")
    assert main(["summary", str(out)]) == 0
    assert {p: p.read_text() for p in out.rglob("SUMMARY.md")} == before


def test_no_targets_is_a_usage_error(tmp_path: Path) -> None:
    assert main(["run", "--root", str(tmp_path), "--out", str(tmp_path / "o")]) == 2


def test_featured_flag_expands(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from inspect_audit.findings import cli

    seen: list[list[str]] = []
    monkeypatch.setattr(cli, "sweep", lambda targets, ctx, producers: seen.append(list(targets)) or {})
    monkeypatch.setattr(cli, "write_outputs", lambda out, runs: None)
    assert main(["run", "--root", str(tmp_path), "--out", str(tmp_path / "o"), "--featured"]) == 0
    assert seen[0] == [f"inspect_evals/{name}" for name in FEATURED]
```

- [ ] **Step 3: Run the tests to see them fail**

Run: `uv run pytest tests/findings/test_cli.py -q`
Expected: `ModuleNotFoundError`.

- [ ] **Step 4: Write the CLI**

`src/inspect_audit/findings/cli.py`:

```python
"""`inspect-audit-findings`: run the deterministic producers over evals and write runs, parquet and summaries."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path

from inspect_ai.log import list_eval_logs

from .adapters import Context, slug
from .adapters import dataset as dataset_adapter
from .adapters import header as header_adapter
from .adapters import lint as lint_adapter
from .featured import FEATURED
from .io import findings_df, read_runs, runs_df, write_parquet, write_run
from .models import Run
from .producers import ProducerConfig
from .render import render_eval_summary, render_sweep_summary

EXTERNAL_PRODUCERS: dict[str, Callable[[str, Context], Run]] = {
    "lint": lint_adapter.run,
    "dataset": dataset_adapter.run,
}


def collect_logs(sources: Sequence[str]) -> list[Path]:
    """Local files and directories, and `hawk:` eval sets fetched through inspect_audit, as log paths."""
    paths: list[Path] = []
    for source in sources:
        location = source
        if source.startswith("hawk:"):
            from inspect_audit._registry import fetch_logs

            location = fetch_logs(source)
        path = Path(location.removeprefix("file://"))
        if path.is_file():
            paths.append(path)
        else:
            paths += [Path(info.name.removeprefix("file://")) for info in list_eval_logs(str(path), recursive=True)]
    return sorted(set(paths))


def sweep(targets: Sequence[str], ctx: Context, producers: set[str]) -> dict[str, list[Run]]:
    """Header first, then the selected external producers, for every target. Nothing raises."""
    runs_by_eval: dict[str, list[Run]] = {}
    for target in targets:
        runs = [header_adapter.run(target, ctx)]
        for name in sorted(producers):
            runs.append(EXTERNAL_PRODUCERS[name](target, ctx))
        runs_by_eval[target] = runs
    return runs_by_eval


_FILE_NAMES = {header_adapter.PRODUCER: "header", lint_adapter.PRODUCER: "lint", dataset_adapter.PRODUCER: "dataset"}


def write_outputs(out: Path, runs_by_eval: Mapping[str, Sequence[Run]]) -> None:
    all_runs: list[Run] = []
    for target, runs in runs_by_eval.items():
        directory = out / slug(target)
        for run in runs:
            write_run(run, directory / f"{_FILE_NAMES.get(run.producer, slug(run.producer))}.run.json")
        (directory / "SUMMARY.md").write_text(render_eval_summary(runs))
        all_runs += runs
    write_parquet(findings_df(all_runs), out / "findings.parquet")
    write_parquet(runs_df(all_runs), out / "runs.parquet")
    (out / "SUMMARY.md").write_text(render_sweep_summary(runs_by_eval))


def _summaries_from_disk(out: Path) -> int:
    runs_by_eval: dict[str, list[Run]] = {}
    for run in read_runs(out):
        runs_by_eval.setdefault(run.subject.eval, []).append(run)
    if not runs_by_eval:
        print(f"no *.run.json under {out}", file=sys.stderr)
        return 2
    write_outputs(out, runs_by_eval)
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="inspect-audit-findings")
    sub = parser.add_subparsers(dest="command", required=True)
    run_p = sub.add_parser("run", help="run the producers over evals")
    run_p.add_argument("--root", required=True, type=Path, help="inspect_evals checkout")
    run_p.add_argument("--logs", action="append", default=[], help="log dir, .eval file, or hawk:<eval-set-id>; repeatable")
    run_p.add_argument("--out", required=True, type=Path)
    run_p.add_argument("--producers", default="lint,dataset", help="external producers to run; the header producer always runs")
    run_p.add_argument("--resolve", action="store_true", help="resolve the task to compare logged sample ids (needs inspect_evals importable)")
    run_p.add_argument("--featured", action="store_true", help="add the 35 Featured evals")
    run_p.add_argument("targets", nargs="*", help="registry names, e.g. inspect_evals/stereoset")
    sum_p = sub.add_parser("summary", help="re-render summaries from existing run files")
    sum_p.add_argument("out", type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "summary":
        return _summaries_from_disk(args.out)

    targets = list(args.targets) + ([f"inspect_evals/{name}" for name in FEATURED] if args.featured else [])
    if not targets:
        print("at least one target or --featured is required", file=sys.stderr)
        return 2
    producers = {name for name in str(args.producers).split(",") if name}
    unknown = producers - set(EXTERNAL_PRODUCERS)
    if unknown:
        print(f"unknown producers: {', '.join(sorted(unknown))}; known: {', '.join(EXTERNAL_PRODUCERS)}", file=sys.stderr)
        return 2
    ctx = Context(
        ie_root=args.root.resolve(),
        logs=collect_logs(args.logs),
        out_dir=args.out.resolve(),
        producers=ProducerConfig.from_env(),
        resolve=bool(args.resolve),
    )
    runs_by_eval = sweep(targets, ctx, producers)
    write_outputs(args.out, runs_by_eval)
    skipped = any(
        run.outcomes and all(outcome.status == "skip" for outcome in run.outcomes)
        for runs in runs_by_eval.values()
        for run in runs
    )
    return 1 if skipped else 0


if __name__ == "__main__":
    raise SystemExit(main())
```

Note `--featured` with no other targets passes the "at least one target" check because the featured names are appended first.

- [ ] **Step 5: Register the console script**

In `pyproject.toml`, after `[project.entry-points.inspect_ai]`, add:

```toml
[project.scripts]
inspect-audit-findings = "inspect_audit.findings.cli:main"
```

Then `uv sync --python 3.13 --extra dev` so the script is installed, and check `uv run inspect-audit-findings --help` prints usage.

- [ ] **Step 6: Run the tests and the gate**

Run: `uv run pytest tests/findings -q && make check && uv run pytest -m "not docker" -q`
Expected: all PASS, checks clean, the whole suite still green.

- [ ] **Step 7: Commit**

```bash
git add src/inspect_audit/findings/featured.py src/inspect_audit/findings/cli.py tests/findings/test_cli.py pyproject.toml uv.lock
git commit -m "$(cat <<'EOF'
Add the inspect-audit-findings CLI

`run` executes the header producer plus selected external producers per
eval and writes run JSON, parquet and markdown summaries; `summary`
re-renders from disk. `--logs` accepts hawk: eval sets via fetch_logs.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
EOF
)"
```

---

### Task 9: Acceptance run

**Files:**
- Create: `agent_artefacts/findings_prototype/ACCEPTANCE.md`
- Create: `agent_artefacts/findings_prototype/out/` (the run outputs, committed except `*.parquet` larger than 5 MB)

**Interfaces:**
- Consumes: the installed CLI.

- [ ] **Step 1: Run the sweep from the inspect_evals environment**

The header adapter needs an inspect_ai that can read the logs and `--resolve` needs inspect_evals importable, so run from the inspect_evals checkout's environment with this worktree overlaid. Build a wheel first so the overlay is not a stale cached build (see the uv path-dep cache note in memory):

```bash
cd /Users/matt/Developer/inspect_ai/inspect_audit/.worktrees/findings-prototype
rm -rf dist && uv build --wheel
cd /Users/matt/Developer/inspect_ai/inspect_evals
uv run --with /Users/matt/Developer/inspect_ai/inspect_audit/.worktrees/findings-prototype/dist/*.whl \
  inspect-audit-findings run --root . --logs logs \
  --out /Users/matt/Developer/inspect_ai/inspect_audit/.worktrees/findings-prototype/agent_artefacts/findings_prototype/out \
  inspect_evals/stereoset inspect_evals/hle inspect_evals/agentharm inspect_evals/xstest \
  inspect_evals/strong_reject inspect_evals/simpleqa
```

Expected: exit 0 or 1 (1 if any producer skipped, which is likely for datasets that need overrides). The first `uvx` invocation of each producer downloads its environment; allow several minutes. If `uvx` cannot build inspect_dataset from git, set `INSPECT_AUDIT_DATASET_CMD="uv run --project /Users/matt/Developer/inspect_ai/inspect-dataset inspect-dataset"` and note it in the acceptance file.

- [ ] **Step 2: Read every summary and write the acceptance note**

Open `out/SUMMARY.md` and each `out/<slug>/SUMMARY.md`. Write `agent_artefacts/findings_prototype/ACCEPTANCE.md` with, per eval: which producers ran or skipped and why; which findings are real, which are noise, and which rule produced the noise; anything the header adapter missed that the header shows; anything the envelope could not express. End with a ranked list titled "What the aggregator needs first". Be concrete: quote rule codes, counts and sample ids.

- [ ] **Step 3: Commit**

```bash
find agent_artefacts/findings_prototype/out -name '*.parquet' -size +5M -delete
git add agent_artefacts/findings_prototype
git commit -m "$(cat <<'EOF'
Record the findings prototype acceptance run over six evals

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
EOF
)"
```

- [ ] **Step 4: Report**

Tell Matt the branch, the acceptance file path, the headline counts per eval, and the top three items from "What the aggregator needs first". Do not push and do not open a PR.
