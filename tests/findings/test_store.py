"""The Store: the only thing that writes review decisions, one object per decision."""

from pathlib import Path

import pytest

from inspect_audit.findings.cli import write_outputs
from inspect_audit.findings.fs import StoreFS
from inspect_audit.findings.models import Run, SampleLocation, Source
from inspect_audit.findings.store import Selection, Store, StoreError

EVAL = "inspect_evals/stereoset"
AUTHOR = "Matt Fisher <matt@example.com>"


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


def _store(tmp_path: Path, run: Run) -> Store:
    root = tmp_path / "findings"
    write_outputs(root, {EVAL: _with_dataset_run(run)})
    return Store(root)


def test_resolve_by_eval_and_rule_or_by_record_id(tmp_path: Path, run: Run) -> None:
    store = _store(tmp_path, run)
    by_rule = store.resolve(Selection(eval=EVAL, rule="duplicate_questions"))
    assert [f.fingerprint for f in by_rule] == ["sha256:dup0", "sha256:dup1", "sha256:dup2"]
    by_id = store.resolve(Selection(ids=("lint-1/1",)))
    assert by_id[0].fingerprint == "sha256:0"
    by_fp = store.resolve(Selection(fingerprints=("sha256:dup1",)))
    assert by_fp[0].id == "dataset-1/2"
    with pytest.raises(StoreError, match="nothing"):
        store.resolve(Selection(eval=EVAL, rule="nope"))
    with pytest.raises(StoreError, match="unknown"):
        store.resolve(Selection(ids=("lint-1/99",)))
    with pytest.raises(StoreError, match="unknown fingerprint"):
        store.resolve(Selection(fingerprints=("sha256:nope",)))
    with pytest.raises(StoreError, match="rule, record ids or fingerprints"):
        store.resolve(Selection(eval=EVAL))


def test_suppress_writes_one_decision_and_rerenders(tmp_path: Path, run: Run) -> None:
    store = _store(tmp_path, run)
    decision = store.suppress(
        rule="duplicate_questions", subject=EVAL, author=AUTHOR, reason="known duplicates"
    )
    assert decision.kind == "suppress" and decision.author == AUTHOR
    assert store.fs.glob("review/*.json") == [decision.key()]
    assert store.review().suppressions[0].rule == "duplicate_questions"
    summary = store.fs.read_text("inspect-evals-stereoset/SUMMARY.md")
    assert "duplicate_questions · 3 observations · false_positive" in summary
    assert store.fs.read_text("suppressions.yaml").startswith("# derived from review/")


def test_accept_assigns_an_id_and_links_by_fingerprint(tmp_path: Path, run: Run) -> None:
    store = _store(tmp_path, run)
    decision = store.accept(
        Selection(eval=EVAL, rule="duplicate_questions"),
        title="dups",
        author=AUTHOR,
        reason="checked",
    )
    assert decision.accept is not None and decision.accept.issue == "ISS-0001"
    assert decision.accept.fingerprints == ["sha256:dup0", "sha256:dup1", "sha256:dup2"]
    second = store.accept(Selection(ids=("lint-1/1",)), title="filter", author=AUTHOR)
    assert second.accept is not None and second.accept.issue == "ISS-0002"
    assert [i.id for i in store.review().issues] == ["ISS-0001", "ISS-0002"]
    assert "ISS-0002" in store.fs.read_text("inspect-evals-stereoset/SUMMARY.md")


def test_accept_refuses_an_owned_fingerprint_and_writes_nothing(tmp_path: Path, run: Run) -> None:
    store = _store(tmp_path, run)
    store.accept(Selection(eval=EVAL, rule="duplicate_questions"), title="dups", author=AUTHOR)
    before = store.fs.glob("review/*.json")
    with pytest.raises(StoreError, match="ISS-0001"):
        store.accept(Selection(ids=("dataset-1/1",)), title="again", author=AUTHOR)
    assert store.fs.glob("review/*.json") == before


def test_link_records_the_url(tmp_path: Path, run: Run) -> None:
    store = _store(tmp_path, run)
    accepted = store.accept(Selection(ids=("lint-1/1",)), title="filter", author=AUTHOR)
    assert accepted.accept is not None
    url = "https://github.com/UKGovernmentBEIS/inspect_evals/issues/1"
    store.link(accepted.accept.issue, url, author=AUTHOR)
    assert store.review().issues[0].github == url
    with pytest.raises(StoreError, match="ISS-9999"):
        store.link("ISS-9999", url, author=AUTHOR)


def test_retract_undoes_a_decision(tmp_path: Path, run: Run) -> None:
    store = _store(tmp_path, run)
    sup = store.suppress(rule="IEBP008", author=AUTHOR, reason="r")
    assert store.review().suppressions
    store.retract(sup.id, author=AUTHOR, reason="was wrong")
    assert store.review().suppressions == []
    with pytest.raises(StoreError, match="unknown decision"):
        store.retract("dec-nope", author=AUTHOR, reason="r")


def test_set_status_by_record_id(tmp_path: Path, run: Run) -> None:
    store = _store(tmp_path, run)
    store.set_status(
        Selection(ids=("lint-1/1",)), status="retracted", author=AUTHOR, reason="fixed upstream"
    )
    finding = next(f for r in store.reviewed() for f in r.findings if f.fingerprint == "sha256:0")
    assert finding.status == "retracted"
    assert finding.history[-1].provenance.reason == "fixed upstream"


def test_author_must_carry_an_email(tmp_path: Path, run: Run) -> None:
    store = _store(tmp_path, run)
    with pytest.raises(StoreError, match="Name <email>"):
        store.suppress(rule="IEBP008", author="matt", reason="r")
    assert store.fs.glob("review/*.json") == []


def test_suppress_refuses_a_rule_with_no_current_observation(tmp_path: Path, run: Run) -> None:
    store = _store(tmp_path, run)
    with pytest.raises(StoreError, match="nothing"):
        store.suppress(rule="IEBP08", author=AUTHOR, reason="typo")
    with pytest.raises(StoreError, match="nothing"):
        store.suppress(rule="IEBP008", producer="inspect_dataset", author=AUTHOR, reason="r")
    assert store.fs.glob("review/*.json") == []


def test_store_works_on_a_memory_locator(run: Run) -> None:
    fs = StoreFS.from_locator("memory://store-memory")
    write_outputs(fs, {EVAL: _with_dataset_run(run)})
    store = Store(fs)
    store.suppress(rule="IEBP008", author=AUTHOR, reason="r")
    assert len(store.fs.glob("review/*.json")) == 1 and store.review().suppressions


def test_issue_ids_are_never_reused_after_a_retraction(tmp_path: Path, run: Run) -> None:
    store = _store(tmp_path, run)
    first = store.accept(Selection(ids=("lint-1/1",)), title="t", author=AUTHOR)
    store.retract(first.id, author=AUTHOR, reason="wrong")
    second = store.accept(Selection(ids=("lint-1/1",)), title="t again", author=AUTHOR)
    assert second.accept is not None and second.accept.issue == "ISS-0002"
