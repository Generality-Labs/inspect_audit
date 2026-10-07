"""The Store: the only thing that writes review files, with the reviewer as author."""

import subprocess
from datetime import UTC, datetime
from pathlib import Path

import pytest

from inspect_audit.findings.cli import write_outputs
from inspect_audit.findings.models import Run, SampleLocation, Source
from inspect_audit.findings.review import load_review
from inspect_audit.findings.store import Selection, Store, StoreError

EVAL = "inspect_evals/stereoset"
AUTHOR = "Matt Fisher <matt@example.com>"


def _git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(root), *args], capture_output=True, text=True, check=True
    ).stdout.strip()


def _with_dataset_run(run: Run) -> list[Run]:
    base = run.findings[0]
    findings = [
        base.model_copy(
            update={
                "id": None,  # the run assigns dataset-1/<n>
                "run_id": "dataset-1",
                "producer": "inspect_dataset",
                "rule": "duplicate_questions",
                "fingerprint": f"sha256:dup{i}",
                "locations": [SampleLocation(role="primary", dataset="d", sample_id=str(i))],
                "source": Source(format="x", record={}),
            }
        )
        for i in range(3)
    ]
    dataset = run.model_copy(
        update={"id": "dataset-1", "producer": "inspect_dataset", "findings": findings}
    )
    return [run, Run.model_validate(dataset.model_dump())]


def _store(tmp_path: Path, run: Run, *, git: bool = True) -> Store:
    root = tmp_path / "findings"
    root.mkdir()
    write_outputs(root, {EVAL: _with_dataset_run(run)})
    if git:  # the sweep output is committed, as the scheduled run would
        _git(root, "init", "-q")
        _git(root, "add", "-A")
        _git(
            root,
            "-c",
            "user.name=bot",
            "-c",
            "user.email=bot@example.com",
            "commit",
            "-q",
            "-m",
            "sweep",
        )
    return Store(root)


def test_resolve_by_eval_and_rule_or_by_record_id(tmp_path: Path, run: Run) -> None:
    store = _store(tmp_path, run)
    by_rule = store.resolve(Selection(eval=EVAL, rule="duplicate_questions"))
    assert [f.fingerprint for f in by_rule] == ["sha256:dup0", "sha256:dup1", "sha256:dup2"]
    by_id = store.resolve(Selection(ids=("lint-1/1",)))
    assert by_id[0].fingerprint == "sha256:0"
    with pytest.raises(StoreError, match="nothing"):
        store.resolve(Selection(eval=EVAL, rule="nope"))
    with pytest.raises(StoreError, match="unknown"):
        store.resolve(Selection(ids=("lint-1/99",)))
    with pytest.raises(StoreError, match="rule or record ids"):
        store.resolve(Selection(eval=EVAL))


def test_suppress_writes_renders_and_commits_as_the_reviewer(tmp_path: Path, run: Run) -> None:
    store = _store(tmp_path, run)
    rule = store.suppress(
        rule="duplicate_questions", subject=EVAL, author=AUTHOR, reason="known duplicates"
    )
    assert rule.since == datetime.now(tz=UTC).date()
    assert load_review(store.root).suppressions == [rule]
    summary = (store.root / "inspect-evals-stereoset" / "SUMMARY.md").read_text()
    assert "duplicate_questions · 3 observations · false_positive" in summary
    assert _git(store.root, "log", "-1", "--format=%an <%ae>") == AUTHOR
    assert _git(store.root, "log", "-1", "--format=%s").startswith("review: suppress")
    assert _git(store.root, "status", "--porcelain") == ""


def test_accept_assigns_an_id_and_links_by_fingerprint(tmp_path: Path, run: Run) -> None:
    store = _store(tmp_path, run)
    issue = store.accept(
        Selection(eval=EVAL, rule="duplicate_questions"),
        title="dups",
        author=AUTHOR,
        reason="checked",
    )
    assert issue.id == "ISS-0001" and issue.subject == EVAL
    assert issue.findings == ["sha256:dup0", "sha256:dup1", "sha256:dup2"]
    second = store.accept(Selection(ids=("lint-1/1",)), title="filter", author=AUTHOR)
    assert second.id == "ISS-0002"
    assert [i.id for i in load_review(store.root).issues] == ["ISS-0001", "ISS-0002"]
    assert "ISS-0002" in (store.root / "inspect-evals-stereoset" / "SUMMARY.md").read_text()


def test_accept_refuses_a_finding_another_issue_owns(tmp_path: Path, run: Run) -> None:
    store = _store(tmp_path, run)
    store.accept(Selection(eval=EVAL, rule="duplicate_questions"), title="dups", author=AUTHOR)
    before = (store.root / "issues.yaml").read_text()
    with pytest.raises(StoreError, match="ISS-0001"):
        store.accept(Selection(ids=("dataset-1/1",)), title="again", author=AUTHOR)
    assert (store.root / "issues.yaml").read_text() == before


def test_link_records_the_url(tmp_path: Path, run: Run) -> None:
    store = _store(tmp_path, run)
    issue = store.accept(Selection(ids=("lint-1/1",)), title="filter", author=AUTHOR)
    url = "https://github.com/UKGovernmentBEIS/inspect_evals/issues/1"
    assert store.link(issue.id, url, author=AUTHOR).github == url
    assert load_review(store.root).issues[0].github == url
    with pytest.raises(StoreError, match="ISS-9999"):
        store.link("ISS-9999", url, author=AUTHOR)


def test_store_without_git_writes_but_does_not_commit(tmp_path: Path, run: Run) -> None:
    store = _store(tmp_path, run, git=False)
    store.suppress(rule="IEBP008", author=AUTHOR, reason="r")
    assert (store.root / "suppressions.yaml").is_file()
    assert not (store.root / ".git").exists()


def test_author_must_carry_an_email(tmp_path: Path, run: Run) -> None:
    store = _store(tmp_path, run)
    with pytest.raises(StoreError, match="Name <email>"):
        store.suppress(rule="IEBP008", author="matt", reason="r")
    assert not (store.root / "suppressions.yaml").exists()


def test_relative_paths_commit_in_the_review_dir(
    tmp_path: Path, run: Run, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = _store(tmp_path, run)
    monkeypatch.chdir(tmp_path)
    relative = Store(Path("findings"))
    relative.suppress(rule="IEBP008", author=AUTHOR, reason="r")
    assert _git(store.root, "log", "-1", "--format=%s") == "review: suppress IEBP008 on *"
    assert _git(store.root, "status", "--porcelain") == ""


def test_review_commit_leaves_unrelated_staged_work_alone(tmp_path: Path, run: Run) -> None:
    store = _store(tmp_path, run)
    (store.root / "other.txt").write_text("not a review decision\n")
    _git(store.root, "add", "other.txt")
    store.suppress(rule="IEBP008", author=AUTHOR, reason="r")
    committed = _git(store.root, "show", "--name-only", "--format=", "HEAD").splitlines()
    assert "suppressions.yaml" in committed and "other.txt" not in committed
    assert _git(store.root, "status", "--porcelain") == "A  other.txt"


def test_gitignored_store_dir_is_written_but_not_committed(tmp_path: Path, run: Run) -> None:
    _git(tmp_path, "init", "-q")
    (tmp_path / ".gitignore").write_text("out/\n")
    root = tmp_path / "out"
    write_outputs(root, {EVAL: [run]})
    Store(root).suppress(rule="IEBP008", author=AUTHOR, reason="r")
    assert (root / "suppressions.yaml").is_file()
    assert _git(tmp_path, "rev-list", "--all", "--count") == "0"


def test_a_decision_that_changes_nothing_is_not_an_error(tmp_path: Path, run: Run) -> None:
    store = _store(tmp_path, run)
    issue = store.accept(Selection(ids=("lint-1/1",)), title="filter", author=AUTHOR)
    url = "https://github.com/x/y/issues/1"
    store.link(issue.id, url, author=AUTHOR)
    store.link(issue.id, url, author=AUTHOR)  # same again: nothing to commit, no error
    assert _git(store.root, "rev-list", "--count", "HEAD") == "3"


def test_a_failed_commit_reports_what_git_said(tmp_path: Path, run: Run) -> None:
    store = _store(tmp_path, run)
    hook = store.root / ".git" / "hooks" / "pre-commit"
    hook.write_text("#!/bin/sh\necho 'hook says no'\nexit 1\n")
    hook.chmod(0o755)
    with pytest.raises(StoreError, match="hook says no"):
        store.suppress(rule="IEBP008", author=AUTHOR, reason="r")


def test_suppress_refuses_a_rule_with_no_current_observation(tmp_path: Path, run: Run) -> None:
    store = _store(tmp_path, run)
    with pytest.raises(StoreError, match="nothing"):
        store.suppress(rule="IEBP08", author=AUTHOR, reason="typo")
    with pytest.raises(StoreError, match="nothing"):
        store.suppress(rule="IEBP008", producer="inspect_dataset", author=AUTHOR, reason="r")
    assert not (store.root / "suppressions.yaml").exists()


def test_review_commit_includes_export_files_that_were_never_committed(
    tmp_path: Path, run: Run
) -> None:
    store = _store(tmp_path, run)
    _git(store.root, "rm", "-r", "-q", "--cached", "export")
    _git(store.root, "-c", "user.name=b", "-c", "user.email=b@x", "commit", "-q", "-m", "untrack")
    store.suppress(rule="IEBP008", author=AUTHOR, reason="r")
    committed = _git(store.root, "show", "--name-only", "--format=", "HEAD").splitlines()
    assert "export/index.json" in committed
    assert _git(store.root, "status", "--porcelain") == ""
