"""Slicing real logs down to one item.

The premise of the case filesystem is that what lands in the sandbox is a genuine
Inspect log, not a summary of one. So these tests read the slice back through
Inspect's own API — if it were subtly not a log, `read_eval_log` is what would notice.
"""

from pathlib import Path

from inspect_ai.log import (
    read_eval_log,
    read_eval_log_sample,
    read_eval_log_sample_summaries,
)
from test_helpers.logs import run_fixture_eval

from inspect_audit._item import AttemptRef, sample_logs


def refs(log: str, sample_id: str, *epochs: int) -> list[AttemptRef]:
    return [
        AttemptRef(model="mockllm/model", epoch=epoch, log_file=log, sample_id=sample_id)
        for epoch in epochs
    ]


def test_slice_is_a_real_log_holding_only_the_audited_item(
    fixture_log: str, tmp_path: Path
) -> None:
    files, _ = sample_logs(refs(fixture_log, "2", 1), stage=tmp_path)
    sliced = next(iter(files.values()))

    log = read_eval_log(sliced)
    assert log.status == "success"
    assert [(s.id, s.epoch) for s in (log.samples or [])] == [(2, 1)]
    # The cheap reading paths work too, which is how an auditor will actually use it.
    assert [(s.id, s.epoch) for s in read_eval_log_sample_summaries(sliced)] == [(2, 1)]
    assert read_eval_log_sample(sliced, id=2, epoch=1).input == "q2"


def test_header_survives_verbatim(fixture_log: str, tmp_path: Path) -> None:
    """The header is the point: it records how the attempt was elicited and graded.

    Recovering those facts ourselves would hand the auditor a schema of ours to trust,
    so this is equality against the source header rather than a spot check.
    """
    files, _ = sample_logs(refs(fixture_log, "1", 1), stage=tmp_path)
    before = read_eval_log(fixture_log, header_only=True)
    after = read_eval_log(next(iter(files.values())), header_only=True)

    assert after.eval == before.eval  # task, model, scorers, config, packages, args
    assert after.plan == before.plan  # solver chain and generate config


def test_every_epoch_of_an_item_stays_together(
    fixture_log_epochs: str, tmp_path: Path
) -> None:
    files, _ = sample_logs(refs(fixture_log_epochs, "1", 1, 2, 3), stage=tmp_path)
    log = read_eval_log(next(iter(files.values())))
    assert sorted(s.epoch for s in (log.samples or [])) == [1, 2, 3]


def test_one_file_per_source_log_keeping_its_name(fixture_log: str, tmp_path: Path) -> None:
    """A case reads like the logs it came from, so names are not rewritten."""
    other = run_fixture_eval(str(tmp_path / "other"), name="other_task")
    files, _ = sample_logs(
        refs(fixture_log, "1", 1) + refs(other, "1", 1), stage=tmp_path / "case"
    )

    assert {Path(p).name for p in files} == {
        Path(log.replace("file://", "")).name for log in (fixture_log, other)
    }


def test_an_unreadable_log_costs_one_model_not_the_case(
    fixture_log: str, tmp_path: Path
) -> None:
    files, _ = sample_logs(
        refs(fixture_log, "1", 1) + refs(str(tmp_path / "missing.eval"), "1", 1),
        stage=tmp_path / "case",
    )
    assert len(files) == 1
