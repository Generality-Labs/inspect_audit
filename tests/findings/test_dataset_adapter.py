"""inspect-dataset scan output -> Run."""

import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest
from test_adapters_common import STUBS, make_root

from inspect_audit.findings.adapters import Context, subject_for
from inspect_audit.findings.adapters.dataset import (
    DATASET_OVERRIDES,
    PRODUCER,
    hf_asset,
    parse,
    run,
)
from inspect_audit.findings.models import SampleLocation
from inspect_audit.findings.producers import ProducerConfig

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
    assert isinstance(dup.source.record, dict)
    assert str(dup.source.record["explanation"]).startswith("Question appears 2 times")
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
