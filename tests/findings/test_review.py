"""Review decisions live beside the runs and are applied to copies at render time."""

from datetime import date
from pathlib import Path

import pytest
from pydantic import ValidationError

from inspect_audit.findings.models import Run
from inspect_audit.findings.review import (
    IssueEntry,
    Review,
    SuppressionRule,
    apply_review,
    load_review,
    unmatched_issue_findings,
)


def _rule(**overrides: object) -> SuppressionRule:
    data: dict[str, object] = {
        "rule": "IEBP008",
        "author": "matt",
        "reason": "struct answers",
        "since": date(2026, 9, 30),
    }
    data.update(overrides)
    return SuppressionRule.model_validate(data)


def _issue(**overrides: object) -> IssueEntry:
    data: dict[str, object] = {
        "id": "ISS-1",
        "title": "t",
        "subject": "inspect_evals/x",
        "findings": ["sha256:0"],
        "author": "m",
        "opened": date(2026, 9, 30),
    }
    data.update(overrides)
    return IssueEntry.model_validate(data)


def test_load_review_reads_both_files_and_tolerates_missing_ones(tmp_path: Path) -> None:
    assert load_review(tmp_path) == Review()
    (tmp_path / "suppressions.yaml").write_text(
        "- rule: answer_length\n  subject: inspect_evals/stereoset\n  producer: inspect_dataset\n"
        "  kind: false_positive\n  author: matt\n  reason: struct-typed answers have no length\n"
        "  since: 2026-09-30\n"
    )
    (tmp_path / "issues.yaml").write_text(
        "- id: ISS-0001\n  title: strong_reject records 313 samples where eval.yaml declares 324\n"
        "  subject: inspect_evals/strong_reject\n  findings: [sha256:aaa, sha256:bbb]\n"
        "  author: matt\n  opened: 2026-09-30\n"
        "  github: https://github.com/UKGovernmentBEIS/inspect_evals/issues/1\n"
    )
    review = load_review(tmp_path)
    assert review.suppressions[0].rule == "answer_length"
    assert review.suppressions[0].since == date(2026, 9, 30)
    assert review.issues[0].id == "ISS-0001"
    assert review.issues[0].findings == ["sha256:aaa", "sha256:bbb"]


def test_unknown_keys_and_empty_issues_are_rejected_naming_the_file(tmp_path: Path) -> None:
    (tmp_path / "suppressions.yaml").write_text(
        "- rule: x\n  author: m\n  reason: r\n  since: 2026-09-30\n  becuase: typo\n"
    )
    with pytest.raises(ValueError, match=r"suppressions\.yaml"):
        load_review(tmp_path)
    (tmp_path / "suppressions.yaml").unlink()
    (tmp_path / "issues.yaml").write_text(
        "- id: ISS-1\n  title: t\n  subject: inspect_evals/x\n  findings: []\n  author: m\n"
        "  opened: 2026-09-30\n"
    )
    with pytest.raises(ValueError, match=r"issues\.yaml"):
        load_review(tmp_path)


def test_a_fingerprint_in_two_issues_is_rejected() -> None:
    with pytest.raises(ValidationError, match="sha256:0"):
        Review(issues=[_issue(), _issue(id="ISS-2")])


def test_apply_review_suppresses_matching_findings_on_copies(run: Run) -> None:
    review = Review(suppressions=[_rule(subject="inspect_evals/stereoset")])
    applied = apply_review([run], review)
    assert run.findings[0].suppressions == []  # the original is untouched
    suppression = applied[0].findings[0].suppressions[0]
    assert suppression.kind == "false_positive"
    assert suppression.provenance.author == "matt"
    assert suppression.provenance.reason == "struct answers"
    assert suppression.provenance.timestamp.date() == date(2026, 9, 30)


def test_suppression_scope_by_subject_and_producer(run: Run) -> None:
    def suppressed(rule: SuppressionRule) -> bool:
        return bool(apply_review([run], Review(suppressions=[rule]))[0].findings[0].suppressions)

    assert suppressed(_rule(subject="*"))
    assert not suppressed(_rule(subject="inspect_evals/hle"))
    assert not suppressed(_rule(producer="inspect_dataset"))
    assert suppressed(_rule(producer="inspect_evals_lint"))
    assert not suppressed(_rule(rule="IEBP999"))


def test_apply_review_links_issues_by_fingerprint_and_reports_unmatched(run: Run) -> None:
    review = Review(
        issues=[
            _issue(
                id="ISS-0007",
                subject="inspect_evals/stereoset",
                findings=["sha256:0", "sha256:gone"],
            )
        ]
    )
    applied = apply_review([run], review)
    assert applied[0].findings[0].issue == "ISS-0007"
    assert run.findings[0].issue is None
    assert unmatched_issue_findings(review, applied) == {"ISS-0007": ["sha256:gone"]}


def test_yaml_syntax_error_is_a_value_error_naming_the_file(tmp_path: Path) -> None:
    (tmp_path / "suppressions.yaml").write_text("- [unclosed\n")
    with pytest.raises(ValueError, match=r"suppressions\.yaml"):
        load_review(tmp_path)


def test_duplicate_issue_ids_are_rejected() -> None:
    with pytest.raises(ValidationError, match="ISS-1"):
        Review(issues=[_issue(), _issue(findings=["sha256:other"])])
