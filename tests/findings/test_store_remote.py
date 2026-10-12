"""Against a real S3-compatible store, when INSPECT_AUDIT_TEST_STORE names one. Skips otherwise.

Set INSPECT_AUDIT_TEST_STORE to a prefix such as s3://<bucket>/ci, with AWS_ENDPOINT_URL,
AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY for the bucket. Each run writes under a fresh
sub-prefix and leaves it there; a lifecycle rule on the prefix is the cleanup.
"""

import os
import uuid

import pytest

from inspect_audit.findings.cli import write_outputs
from inspect_audit.findings.fs import StoreFS
from inspect_audit.findings.io import read_current
from inspect_audit.findings.models import Run
from inspect_audit.findings.store import Selection, Store

LOCATOR = os.environ.get("INSPECT_AUDIT_TEST_STORE")
pytestmark = pytest.mark.skipif(not LOCATOR, reason="INSPECT_AUDIT_TEST_STORE not set")


def test_round_trip_on_a_real_bucket(run: Run) -> None:
    fs = StoreFS.from_locator(f"{LOCATOR}/test-{uuid.uuid4().hex[:8]}")
    write_outputs(fs, {"inspect_evals/stereoset": [run]})
    assert [r.id for r in read_current(fs)] == ["lint-1"]
    Store(fs).accept(Selection(ids=("lint-1/1",)), title="t", author="Test Runner <t@example.com>")
    assert fs.is_file("export/index.json") and len(fs.glob("review/*.json")) == 1
    assert "ISS-0001" in fs.read_text("inspect-evals-stereoset/SUMMARY.md")
