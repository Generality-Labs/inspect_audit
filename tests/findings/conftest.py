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
