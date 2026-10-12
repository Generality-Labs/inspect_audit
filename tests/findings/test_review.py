"""Review decisions are an append-only log of objects, folded into the Review applied at render time."""

from datetime import UTC, date, datetime
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from inspect_audit.findings.fs import StoreFS
from inspect_audit.findings.models import Run
from inspect_audit.findings.review import (
    Decision,
    IssueEntry,
    LinkPayload,
    Review,
    SuppressionRule,
    SuppressPayload,
    apply_review,
    fold,
    load_decisions,
    load_review,
    migrate_review_files,
    new_decision_id,
    unmatched_issue_findings,
    write_decision,
    write_review_views,
)

T1 = datetime(2026, 10, 9, 4, 0, tzinfo=UTC)
T2 = datetime(2026, 10, 9, 5, 0, tzinfo=UTC)
T3 = datetime(2026, 10, 9, 6, 0, tzinfo=UTC)


def _dec(
    kind: str,
    at: datetime,
    author: str = "Matt Fisher <m@x>",
    reason: str | None = "r",
    **payload: object,
) -> Decision:
    return Decision.model_validate(
        {
            "id": new_decision_id(at),
            "at": at,
            "author": author,
            "kind": kind,
            "reason": reason,
            kind: payload,
        }
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


def test_duplicate_issue_ids_are_rejected() -> None:
    with pytest.raises(ValidationError, match="ISS-1"):
        Review(issues=[_issue(), _issue(findings=["sha256:other"])])


def test_decision_requires_exactly_the_payload_named_by_kind() -> None:
    with pytest.raises(ValidationError, match="payload"):
        Decision(id="dec-1", at=T1, author="m <m@x>", kind="suppress")
    with pytest.raises(ValidationError, match="payload"):
        Decision(
            id="dec-1",
            at=T1,
            author="m <m@x>",
            kind="suppress",
            suppress=SuppressPayload(rule="r"),
            link=LinkPayload(issue="ISS-0001", url="u"),
        )


def test_decision_keys_sort_by_time_then_id() -> None:
    late = _dec("suppress", T2, rule="b")
    early = _dec("suppress", T1, rule="a")
    assert early.key().startswith("review/20261009T040000Z-dec-")
    assert sorted([late.key(), early.key()]) == [early.key(), late.key()]


def test_decisions_fold_in_key_order_whatever_order_they_were_written() -> None:
    fs = StoreFS.from_locator("memory://fold-order")
    write_decision(
        fs, _dec("accept", T2, issue="ISS-0001", title="t", subject="e", fingerprints=["fp1"])
    )
    write_decision(fs, _dec("link", T3, issue="ISS-0001", url="https://x/1"))
    write_decision(
        fs, _dec("suppress", T1, rule="noise", subject="e")
    )  # written last, first in time
    decisions = load_decisions(fs)
    assert [d.kind for d in decisions] == ["suppress", "accept", "link"]
    review = fold(decisions).review
    assert review.suppressions[0].rule == "noise" and review.suppressions[0].since == T1.date()
    assert review.issues[0].github == "https://x/1" and review.issues[0].opened == T2.date()


def test_retraction_removes_a_suppression_and_frees_an_acceptance() -> None:
    sup = _dec("suppress", T1, rule="noise", subject="e")
    acc = _dec("accept", T1, issue="ISS-0001", title="t", subject="e", fingerprints=["fp1"])
    again = _dec("accept", T3, issue="ISS-0002", title="t2", subject="e", fingerprints=["fp1"])
    folded = fold(
        [
            sup,
            acc,
            _dec("retract", T2, decision=sup.id),
            _dec("retract", T2, decision=acc.id),
            again,
        ]
    )
    assert folded.review.suppressions == []
    assert [i.id for i in folded.review.issues] == ["ISS-0002"] and folded.warnings == []


def test_racing_acceptances_keep_the_later_and_warn_naming_both() -> None:
    first = _dec(
        "accept", T1, issue="ISS-0001", title="a", subject="e", fingerprints=["fp1", "fp2"]
    )
    second = _dec("accept", T2, issue="ISS-0002", title="b", subject="e", fingerprints=["fp2"])
    folded = fold([first, second])
    by_id = {i.id: i for i in folded.review.issues}
    assert by_id["ISS-0001"].findings == ["fp1"] and by_id["ISS-0002"].findings == ["fp2"]
    (warning,) = folded.warnings
    assert first.id in warning and second.id in warning and "fp2" in warning


def test_status_decisions_fold_and_apply_with_history(run: Run) -> None:
    decision = _dec(
        "status", T1, fingerprints=["sha256:0"], status="retracted", reason="false alarm"
    )
    folded = fold([decision])
    assert folded.review.statuses == {"sha256:0": "retracted"}
    applied = apply_review([run], folded.review)
    finding = applied[0].findings[0]
    assert finding.status == "retracted" and finding.history[-1].provenance.reason == "false alarm"
    assert run.findings[0].status == "supported"


def test_review_views_are_derived_and_marked(tmp_path: Path) -> None:
    fs = StoreFS.from_locator(tmp_path)
    review = Review(suppressions=[_rule()], issues=[_issue()])
    keys = write_review_views(fs, review)
    assert keys == ["suppressions.yaml", "issues.yaml"]
    for key in keys:
        assert fs.read_text(key).startswith("# derived from review/; do not edit\n")
    assert yaml.safe_load(fs.read_text("issues.yaml"))[0]["id"] == _issue().id


def test_migrate_turns_the_yaml_files_into_decisions_once(tmp_path: Path) -> None:
    fs = StoreFS.from_locator(tmp_path)
    fs.write_text(
        "suppressions.yaml", yaml.safe_dump([_rule().model_dump(mode="json", exclude_none=True)])
    )
    fs.write_text(
        "issues.yaml", yaml.safe_dump([_issue().model_dump(mode="json", exclude_none=True)])
    )
    before = Review(suppressions=[_rule()], issues=[_issue()])
    created = migrate_review_files(fs, now=T3)
    assert [d.kind for d in created] == ["suppress", "accept"]
    assert load_review(fs) == before
    assert migrate_review_files(fs, now=T3) == []
    assert len(fs.glob("review/*.json")) == len(created)


def test_a_malformed_decision_object_names_itself(tmp_path: Path) -> None:
    fs = StoreFS.from_locator(tmp_path)
    fs.write_text("review/20261009T040000Z-dec-bad.json", "{not json")
    with pytest.raises(ValueError, match=r"review/20261009T040000Z-dec-bad\.json"):
        load_decisions(fs)
    fs.write_text(
        "review/20261009T040000Z-dec-bad.json",
        '{"id": "dec-bad", "at": "2026-10-09T04:00:00Z", "author": "m <m@x>", "kind": "suppress",'
        ' "suppress": {"rule": "r", "becuase": "typo"}}',
    )
    with pytest.raises(ValueError, match="becuase"):
        load_decisions(fs)


def test_retracting_a_retraction_restores_the_decision() -> None:
    sup = _dec("suppress", T1, rule="noise", subject="e")
    r1 = _dec("retract", T2, decision=sup.id)
    r2 = _dec("retract", T3, decision=r1.id)
    assert fold([sup, r1]).review.suppressions == []
    assert [s.rule for s in fold([sup, r1, r2]).review.suppressions] == ["noise"]


def test_two_acceptances_of_one_issue_id_keep_the_later_and_warn() -> None:
    first = _dec("accept", T1, issue="ISS-0005", title="x", subject="e", fingerprints=["fp1"])
    second = _dec("accept", T2, issue="ISS-0005", title="y", subject="e", fingerprints=["fp2"])
    folded = fold([first, second])
    assert [(i.id, i.title, i.findings) for i in folded.review.issues] == [
        ("ISS-0005", "y", ["fp2"])
    ]
    (warning,) = folded.warnings
    assert first.id in warning and second.id in warning and "ISS-0005" in warning


def test_migrate_keeps_file_order_distinguishes_twins_and_dates_links_deterministically(
    tmp_path: Path,
) -> None:
    fs = StoreFS.from_locator(tmp_path)
    rules = [
        _rule(rule="ZZZ", reason="first in the file"),
        _rule(rule="AAA", reason="second in the file"),
        _rule(rule="AAA", reason="same payload, another reason"),
    ]
    issues = [
        _issue(id="ISS-0002", findings=["sha256:b"], github="https://x/2"),
        _issue(id="ISS-0001", findings=["sha256:a"]),
    ]
    fs.write_text(
        "suppressions.yaml",
        yaml.safe_dump([r.model_dump(mode="json", exclude_none=True) for r in rules]),
    )
    fs.write_text(
        "issues.yaml",
        yaml.safe_dump([i.model_dump(mode="json", exclude_none=True) for i in issues]),
    )
    created = migrate_review_files(fs, now=T3)
    assert len(created) == 6 and len(fs.glob("review/*.json")) == 6
    review = load_review(fs)
    assert [(s.rule, s.reason) for s in review.suppressions] == [(r.rule, r.reason) for r in rules]
    assert [i.id for i in review.issues] == ["ISS-0002", "ISS-0001"]
    assert review.issues[0].github == "https://x/2"
    link = next(d for d in created if d.link is not None)
    assert link.at.date() == issues[0].opened  # not `now`, so a rerun is a no-op
    assert migrate_review_files(fs, now=datetime(2026, 12, 1, tzinfo=UTC)) == []


def test_hand_written_review_files_block_reading_until_migrated(tmp_path: Path) -> None:
    fs = StoreFS.from_locator(tmp_path)
    fs.write_text(
        "issues.yaml", yaml.safe_dump([_issue().model_dump(mode="json", exclude_none=True)])
    )
    with pytest.raises(ValueError, match="migrate"):
        load_decisions(fs)
    migrate_review_files(fs, now=T3)
    write_review_views(fs, load_review(fs))
    assert [d.kind for d in load_decisions(fs)] == ["accept"]
