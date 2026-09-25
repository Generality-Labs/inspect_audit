"""Log headers -> Run: sample counts, version drift, unscored samples, dirty revisions, unknown ids."""

from datetime import UTC, datetime
from pathlib import Path

from inspect_ai import Task, eval
from inspect_ai.dataset import MemoryDataset, Sample
from inspect_ai.log import read_eval_log
from inspect_ai.scorer import match
from test_adapters_common import make_root

from inspect_audit.findings.adapters import Context, subject_for
from inspect_audit.findings.adapters.header import (
    PRODUCER,
    eval_spec_dict,
    matching_headers,
    parse,
    run,
)
from inspect_audit.findings.models import LogLocation, ScorerLocation

STAMP = datetime(2026, 9, 25, 4, 20, 50, tzinfo=UTC)


def _log(log_dir: Path, *, name: str = "stereoset", version: int | str = 3, samples: int = 3) -> Path:
    task = Task(
        name=name,
        dataset=MemoryDataset([Sample(id=i, input=f"q{i}", target="ANSWER") for i in range(1, samples + 1)]),
        scorer=match(),
        version=version,
    )
    return Path(eval(task, model="mockllm/model", log_dir=str(log_dir), display="none")[0].location.removeprefix("file://"))


def test_matching_on_registry_name_or_task(tmp_path: Path) -> None:
    logs = [_log(tmp_path / "a"), _log(tmp_path / "b", name="hle")]
    matched = matching_headers(logs, "inspect_evals/stereoset")
    assert [path.name for path, _ in matched] == [logs[0].name]
    # an inline Task has no registry name, so this match came from eval.task
    assert read_eval_log(str(logs[0]), header_only=True).eval.task_registry_name in (None, "stereoset")


def test_dataset_samples_mismatch_fires(tmp_path: Path) -> None:
    root = make_root(tmp_path)  # eval.yaml says 2123
    log = _log(tmp_path / "logs", samples=3)
    ctx = Context(ie_root=root, logs=[log])
    result = run("inspect_evals/stereoset", ctx)
    assert result.producer == PRODUCER
    finding = next(f for f in result.findings if f.rule == "header.dataset_samples")
    assert finding.dimension == "dataset" and finding.severity == "minor"
    primary = finding.primary_location
    assert isinstance(primary, LogLocation) and primary.path == "eval.dataset.samples"
    assert finding.source.eval_spec is not None
    dataset = finding.source.eval_spec["dataset"]
    assert isinstance(dataset, dict) and "sample_ids" not in dataset
    assert "3" in finding.summary and "2123" in finding.summary
    assert any(o.rule == "header.dataset_samples" and o.status == "fail" for o in result.outcomes)


def test_dataset_samples_uses_the_matching_task_entry(tmp_path: Path) -> None:
    extra = "  - name: other_task\n    dataset_samples: 3\n"
    root = make_root(tmp_path, extra=extra)  # stereoset: 2123, other_task: 3
    log = _log(tmp_path / "logs", samples=3)
    result = run("inspect_evals/stereoset", Context(ie_root=root, logs=[log]))
    assert any(f.rule == "header.dataset_samples" for f in result.findings)


def test_version_drift_across_logs(tmp_path: Path) -> None:
    root = make_root(tmp_path)
    logs = [_log(tmp_path / "a", version=3), _log(tmp_path / "b", version=4)]
    result = run("inspect_evals/stereoset", Context(ie_root=root, logs=logs))
    drift = [f for f in result.findings if f.rule == "header.version_drift"]
    assert len(drift) == 1
    assert drift[0].dimension == "informativeness"
    assert len(drift[0].locations) == 2
    assert sum(1 for loc in drift[0].locations if loc.role == "primary") == 1


def test_no_drift_with_one_version(tmp_path: Path) -> None:
    root = make_root(tmp_path)
    logs = [_log(tmp_path / "a"), _log(tmp_path / "b")]
    result = run("inspect_evals/stereoset", Context(ie_root=root, logs=logs))
    assert not any(f.rule == "header.version_drift" for f in result.findings)
    assert any(o.rule == "header.version_drift" and o.status == "pass" for o in result.outcomes)


def test_no_logs_is_a_skip(tmp_path: Path) -> None:
    root = make_root(tmp_path)
    result = run("inspect_evals/stereoset", Context(ie_root=root, logs=[_log(tmp_path / "x", name="hle")]))
    assert [o.status for o in result.outcomes] == ["skip"]
    assert "no logs" in (result.outcomes[0].message or "")


def test_unscored_and_dirty_checks_exist(tmp_path: Path) -> None:
    root = make_root(tmp_path)
    log = _log(tmp_path / "logs")
    result = run("inspect_evals/stereoset", Context(ie_root=root, logs=[log]))
    rules = {o.rule for o in result.outcomes}
    assert {"header.dataset_samples", "header.version_drift", "header.unscored_samples", "header.dirty_revision"} <= rules
    assert "header.unknown_sample_ids" not in rules  # --resolve off


def test_unknown_sample_ids_with_resolved_ids(tmp_path: Path) -> None:
    root = make_root(tmp_path)
    log = _log(tmp_path / "logs", samples=3)
    header = read_eval_log(str(log), header_only=True)
    subject = subject_for("inspect_evals/stereoset", Context(ie_root=root))
    result = parse([(log, header)], "inspect_evals/stereoset", subject, {}, timestamp=STAMP, resolved_ids={"1", "2"})
    finding = next(f for f in result.findings if f.rule == "header.unknown_sample_ids")
    assert finding.severity == "major"
    assert "1 of 3" in finding.summary


def test_eval_spec_dict_drops_sample_ids(tmp_path: Path) -> None:
    header = read_eval_log(str(_log(tmp_path / "logs")), header_only=True)
    spec = eval_spec_dict(header)
    dataset = spec["dataset"]
    assert isinstance(dataset, dict) and "sample_ids" not in dataset
    assert spec["task"] == "stereoset"


def test_scorer_location_for_unscored(tmp_path: Path) -> None:
    # exercise the location type even though mockllm scores everything: build a finding via parse on a
    # header whose results are edited to report an unscored sample
    root = make_root(tmp_path)
    log = _log(tmp_path / "logs")
    header = read_eval_log(str(log), header_only=True)
    assert header.results is not None
    header.results.scores[0].unscored_samples = 1
    subject = subject_for("inspect_evals/stereoset", Context(ie_root=root))
    result = parse([(log, header)], "inspect_evals/stereoset", subject, {}, timestamp=STAMP)
    finding = next(f for f in result.findings if f.rule == "header.unscored_samples")
    assert isinstance(finding.primary_location, ScorerLocation)
    assert finding.primary_location.scorer == "match"
