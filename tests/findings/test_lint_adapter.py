"""inspect-evals-lint JSON -> Run."""

import json
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest
from test_adapters_common import STUBS, make_root

from inspect_audit.findings.adapters import Context, subject_for
from inspect_audit.findings.adapters.lint import LINT_RULES, PRODUCER, parse, run
from inspect_audit.findings.io import runs_df
from inspect_audit.findings.models import CodeLocation, Subject
from inspect_audit.findings.producers import ProducerConfig

FIXTURES = Path(__file__).parent / "fixtures"
STAMP = datetime(2026, 9, 25, 4, 20, 50, tzinfo=UTC)


def _subject(tmp_path: Path) -> tuple[Path, Subject]:
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
    assert (primary.file, primary.line, primary.column) == (
        "src/inspect_evals/stereoset/stereoset.py",
        64,
        15,
    )
    assert finding.source.format == "inspect_evals_lint.Diagnostic@0.7.0"
    assert finding.source.record == data["packages"][0]["diagnostics"][0]
    assert finding.fingerprint.startswith("sha256:")
    assert finding.run_id == result.id


def test_parse_refuses_a_document_with_the_wrong_package(tmp_path: Path) -> None:
    _, subject = _subject(tmp_path)
    data = json.loads((FIXTURES / "lint_two_packages.json").read_text())
    result = parse(
        data,
        "inspect_evals/hle",
        subject.model_copy(update={"eval": "inspect_evals/hle"}),
        timestamp=STAMP,
    )
    assert result.findings == []
    assert len(result.outcomes) == 25  # hle's rows only, not stereoset's


def test_parse_with_no_matching_package_is_a_skip(tmp_path: Path) -> None:
    _, subject = _subject(tmp_path)
    data = json.loads((FIXTURES / "lint.json").read_text())
    result = parse(
        data,
        "inspect_evals/gaia",
        subject.model_copy(update={"eval": "inspect_evals/gaia"}),
        timestamp=STAMP,
    )
    assert [(o.status, o.rule) for o in result.outcomes] == [("skip", PRODUCER)]
    assert result.outcomes[0].message is not None and "gaia" in result.outcomes[0].message


def test_rule_table_defaults_and_overrides() -> None:
    assert LINT_RULES["IEBP008"] == ("dataset", "minor")
    assert LINT_RULES["IEBP005"] == ("environment", "minor")
    assert LINT_RULES.get("IEFS001", ("harness", "minor")) == ("harness", "minor")


def test_run_with_a_stubbed_producer(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root, _ = _subject(tmp_path)
    monkeypatch.setenv("STUB_OUTPUT_FILE", str(FIXTURES / "lint.json"))
    ctx = Context(
        ie_root=root, producers=ProducerConfig(lint=(sys.executable, str(STUBS / "echo_file.py")))
    )
    result = run("inspect_evals/stereoset", ctx)
    assert len(result.findings) == 1
    assert result.duration_s is not None and result.duration_s >= 0
    assert result.inputs["argv"][-5:] == [
        "--root",
        str(root),
        "stereoset",
        "--output-format",
        "json",
    ]  # type: ignore[index]


def test_run_with_a_failing_producer_is_a_skip(tmp_path: Path) -> None:
    root, _ = _subject(tmp_path)
    ctx = Context(
        ie_root=root, producers=ProducerConfig(lint=(sys.executable, str(STUBS / "fail.py")))
    )
    result = run("inspect_evals/stereoset", ctx)
    assert result.findings == []
    assert result.outcomes[0].status == "skip"
    assert "boom" in (result.outcomes[0].message or "")


def test_run_with_exit_code_two_and_valid_json_is_still_a_skip(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, _ = _subject(tmp_path)
    monkeypatch.setenv("STUB_OUTPUT_FILE", str(FIXTURES / "lint.json"))
    monkeypatch.setenv("STUB_EXIT", "2")
    ctx = Context(
        ie_root=root, producers=ProducerConfig(lint=(sys.executable, str(STUBS / "echo_file.py")))
    )
    result = run("inspect_evals/stereoset", ctx)
    assert result.outcomes[0].status == "skip"
    assert "exit 2" in (result.outcomes[0].message or "")


def test_run_with_exit_code_one_and_valid_json_parses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # lint exits 1 when any check fails; that is a result, not an error
    root, _ = _subject(tmp_path)
    monkeypatch.setenv("STUB_OUTPUT_FILE", str(FIXTURES / "lint.json"))
    monkeypatch.setenv("STUB_EXIT", "1")
    ctx = Context(
        ie_root=root, producers=ProducerConfig(lint=(sys.executable, str(STUBS / "echo_file.py")))
    )
    result = run("inspect_evals/stereoset", ctx)
    assert len(result.findings) == 1


def test_run_with_malformed_output_is_a_skip(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, _ = _subject(tmp_path)
    bad = tmp_path / "bad.json"
    bad.write_text("[]")  # valid JSON, wrong shape
    monkeypatch.setenv("STUB_OUTPUT_FILE", str(bad))
    ctx = Context(
        ie_root=root, producers=ProducerConfig(lint=(sys.executable, str(STUBS / "echo_file.py")))
    )
    result = run("inspect_evals/stereoset", ctx)
    assert result.findings == []
    assert result.outcomes[0].status == "skip"
    assert "could not parse" in (result.outcomes[0].message or "")


def test_parse_0_9_reads_per_rule_status_so_a_failing_rule_has_an_outcome(tmp_path: Path) -> None:
    # lint 0.9.0 writes `outcomes` only for rules that passed or skipped; a rule with diagnostics
    # appears only under `rules`. Read `rules` so failing rules are counted and a run whose other
    # rules all skipped is not mistaken for a skipped run.
    _, subject = _subject(tmp_path)
    data = json.loads((FIXTURES / "lint_0_9.json").read_text())
    result = parse(data, "inspect_evals/scicode", subject, timestamp=STAMP)
    assert result.producer_version == "0.9.0"
    statuses = {o.rule: o.status for o in result.outcomes}
    assert len(statuses) == 28
    assert statuses["IEBP007"] == "fail"
    assert [o.status for o in result.outcomes].count("pass") == 19
    assert [o.status for o in result.outcomes].count("skip") == 8
    failing = next(o for o in result.outcomes if o.rule == "IEBP007")
    assert failing.message is not None and "2 diagnostic" in failing.message
    assert len(result.findings) == 2 and {f.rule for f in result.findings} == {"IEBP007"}
    assert int(runs_df([result]).iloc[0]["outcomes_fail"]) == 1


def test_parse_0_9_run_with_only_failing_and_skipped_rules_is_not_a_skip(tmp_path: Path) -> None:
    _, subject = _subject(tmp_path)
    data = json.loads((FIXTURES / "lint_0_9.json").read_text())
    package = data["packages"][0]
    package["rules"] = [r for r in package["rules"] if r["status"] != "pass"]
    package["outcomes"] = [o for o in package["outcomes"] if o["status"] != "pass"]
    result = parse(data, "inspect_evals/scicode", subject, timestamp=STAMP)
    statuses = [o.status for o in result.outcomes]
    assert "fail" in statuses and not all(status == "skip" for status in statuses)


def test_parse_0_7_document_without_rules_still_uses_outcomes(tmp_path: Path) -> None:
    _, subject = _subject(tmp_path)
    data = json.loads((FIXTURES / "lint.json").read_text())
    assert "rules" not in data["packages"][0]
    result = parse(data, "inspect_evals/stereoset", subject, timestamp=STAMP)
    assert len(result.outcomes) == 25 and len(result.findings) == 1


def test_sandbox_and_shuffle_rules_are_classified() -> None:
    assert LINT_RULES["IESC001"] == ("environment", "major")
    assert LINT_RULES["IESC002"] == ("environment", "major")
    assert LINT_RULES["IEBP010"] == LINT_RULES["IEBP011"] == ("environment", "minor")
