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
