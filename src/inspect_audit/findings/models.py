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

    @property
    def primary_location(self) -> AnyLocation:
        """The one primary location. A plain property: a computed field would serialise and break round-trips."""
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
