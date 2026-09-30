"""Log headers -> Run: sample counts, version drift, unscored samples, dirty revisions, unknown ids."""

from datetime import UTC, datetime
from pathlib import Path

import pytest
from inspect_ai import Task, eval, task
from inspect_ai.dataset import MemoryDataset, Sample
from inspect_ai.log import read_eval_log
from inspect_ai.scorer import match
from test_adapters_common import MOCK_OK, make_root

from inspect_audit.findings.adapters import Context, subject_for
from inspect_audit.findings.adapters.header import (
    PRODUCER,
    _header_matches,
    eval_spec_dict,
    matching_headers,
    parse,
    run,
    select_headers,
)
from inspect_audit.findings.config import LogFilter
from inspect_audit.findings.models import LogLocation, ScorerLocation

STAMP = datetime(2026, 9, 25, 4, 20, 50, tzinfo=UTC)


def _log(
    log_dir: Path,
    *,
    name: str = "stereoset",
    version: int | str = 3,
    samples: int = 3,
    task_args: dict[str, str] | None = None,
) -> Path:
    @task(name=name)
    def _make(subset: str = "all") -> Task:
        return Task(
            name=name,
            dataset=MemoryDataset(
                [Sample(id=i, input=f"q{i}", target="ANSWER") for i in range(1, samples + 1)]
            ),
            scorer=match(),
            version=version,
        )

    return Path(
        eval(
            _make,
            task_args=task_args or {},
            model="mockllm/model",
            log_dir=str(log_dir),
            display="none",
        )[0].location.removeprefix("file://")
    )


def test_matching_on_registry_name_or_task(tmp_path: Path) -> None:
    logs = [_log(tmp_path / "a"), _log(tmp_path / "b", name="hle")]
    matched = matching_headers(logs, "inspect_evals/stereoset")
    assert [path.name for path, _ in matched] == [logs[0].name]
    # an inline Task has no registry name, so this match came from eval.task
    assert read_eval_log(str(logs[0]), header_only=True).eval.task_registry_name in (
        None,
        "stereoset",
    )


def test_dataset_samples_mismatch_fires(tmp_path: Path) -> None:
    root = make_root(tmp_path)  # eval.yaml says 2123
    log = _log(tmp_path / "logs", samples=3)
    ctx = Context(ie_root=root, logs=[log], config=MOCK_OK)
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
    # other_task comes FIRST and its count matches the log, so a first-entry implementation would stay
    # silent; only matching on the log's own task name (stereoset: 2123) makes the check fire
    root = make_root(tmp_path)
    (root / "src" / "inspect_evals" / "stereoset" / "eval.yaml").write_text(
        'title: StereoSet\nversion: "3-A"\ntasks:\n  - name: other_task\n    dataset_samples: 3\n'
        "  - name: stereoset\n    dataset_samples: 2123\n"
    )
    log = _log(tmp_path / "logs", samples=3)
    result = run("inspect_evals/stereoset", Context(ie_root=root, logs=[log], config=MOCK_OK))
    assert any(f.rule == "header.dataset_samples" for f in result.findings)


def test_multi_task_package_matches_logs_by_yaml_task_names(tmp_path: Path) -> None:
    # lab_bench has no task called lab_bench; its logs are named after its tasks
    root = make_root(tmp_path)
    package = root / "src" / "inspect_evals" / "lab_bench"
    package.mkdir(parents=True)
    (package / "eval.yaml").write_text(
        'title: LAB-Bench\nversion: "1-A"\ntasks:\n  - name: lab_bench_litqa\n    dataset_samples: 199\n'
        "  - name: lab_bench_suppqa\n    dataset_samples: 82\n"
    )
    log = _log(tmp_path / "logs", name="lab_bench_litqa", samples=3)
    result = run("inspect_evals/lab_bench", Context(ie_root=root, logs=[log], config=MOCK_OK))
    assert not any(o.status == "skip" for o in result.outcomes)
    finding = next(f for f in result.findings if f.rule == "header.dataset_samples")
    assert "199" in finding.summary


def test_resolve_skips_when_the_resolved_dataset_has_no_ids(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import inspect_audit._resolve as resolve_module

    root = make_root(tmp_path)
    log = _log(tmp_path / "logs", samples=3)
    idless = Task(dataset=MemoryDataset([Sample(input="q") for _ in range(3)]), scorer=match())
    monkeypatch.setattr(resolve_module, "resolve_task", lambda spec, args=None: idless)
    result = run("inspect_evals/stereoset", Context(ie_root=root, logs=[log], resolve=True, config=MOCK_OK))
    assert not any(f.rule == "header.unknown_sample_ids" for f in result.findings)
    skip = next(o for o in result.outcomes if o.rule == "header.unknown_sample_ids")
    assert skip.status == "skip" and "no ids" in (skip.message or "")


def test_resolve_compares_logged_ids_against_the_resolved_dataset(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import inspect_audit._resolve as resolve_module

    root = make_root(tmp_path)
    log = _log(tmp_path / "logs", samples=3)  # ids 1, 2, 3
    smaller = Task(dataset=MemoryDataset([Sample(id=i, input="q") for i in (1, 2)]), scorer=match())
    monkeypatch.setattr(resolve_module, "resolve_task", lambda spec, args=None: smaller)
    result = run("inspect_evals/stereoset", Context(ie_root=root, logs=[log], resolve=True, config=MOCK_OK))
    finding = next(f for f in result.findings if f.rule == "header.unknown_sample_ids")
    assert "1 of 3" in finding.summary


def test_version_drift_across_logs(tmp_path: Path) -> None:
    root = make_root(tmp_path)
    logs = [_log(tmp_path / "a", version=3), _log(tmp_path / "b", version=4)]
    result = run("inspect_evals/stereoset", Context(ie_root=root, logs=logs, config=MOCK_OK))
    drift = [f for f in result.findings if f.rule == "header.version_drift"]
    assert len(drift) == 1
    assert drift[0].dimension == "informativeness"
    assert len(drift[0].locations) == 2
    assert sum(1 for loc in drift[0].locations if loc.role == "primary") == 1


def test_no_drift_with_one_version(tmp_path: Path) -> None:
    root = make_root(tmp_path)
    logs = [_log(tmp_path / "a"), _log(tmp_path / "b")]
    result = run("inspect_evals/stereoset", Context(ie_root=root, logs=logs, config=MOCK_OK))
    assert not any(f.rule == "header.version_drift" for f in result.findings)
    assert any(o.rule == "header.version_drift" and o.status == "pass" for o in result.outcomes)


def test_no_logs_is_a_skip(tmp_path: Path) -> None:
    root = make_root(tmp_path)
    result = run(
        "inspect_evals/stereoset", Context(ie_root=root, logs=[_log(tmp_path / "x", name="hle")], config=MOCK_OK)
    )
    assert [o.status for o in result.outcomes] == ["skip"]
    assert "no logs" in (result.outcomes[0].message or "")


def test_unscored_and_dirty_checks_exist(tmp_path: Path) -> None:
    root = make_root(tmp_path)
    log = _log(tmp_path / "logs")
    result = run("inspect_evals/stereoset", Context(ie_root=root, logs=[log], config=MOCK_OK))
    rules = {o.rule for o in result.outcomes}
    assert {
        "header.dataset_samples",
        "header.version_drift",
        "header.unscored_samples",
        "header.dirty_revision",
    } <= rules
    assert "header.unknown_sample_ids" not in rules  # --resolve off


def test_unknown_sample_ids_with_resolved_ids(tmp_path: Path) -> None:
    root = make_root(tmp_path)
    log = _log(tmp_path / "logs", samples=3)
    header = read_eval_log(str(log), header_only=True)
    subject = subject_for("inspect_evals/stereoset", Context(ie_root=root))
    result = parse(
        [(log, header)],
        "inspect_evals/stereoset",
        subject,
        {},
        timestamp=STAMP,
        resolved_ids={"1", "2"},
    )
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


def test_findings_carry_the_logs_revision_and_the_run_records_the_comparison(
    tmp_path: Path,
) -> None:
    root = make_root(tmp_path)  # eval.yaml says version 3-A
    log = _log(tmp_path / "logs", version=2, samples=3)
    header = read_eval_log(str(log), header_only=True)
    result = run("inspect_evals/stereoset", Context(ie_root=root, logs=[log], config=MOCK_OK))
    finding = next(f for f in result.findings if f.rule == "header.dataset_samples")
    # the finding is about the code that produced the log, not the checkout being compared against
    assert finding.subject.task_version is not None and finding.subject.task_version.full == "2"
    assert finding.subject.revision.commit == (
        header.eval.revision.commit if header.eval.revision else None
    )
    assert finding.subject.task_args == dict(header.eval.task_args or {})
    # the run says what it compared against
    assert result.subject.task_version is not None and result.subject.task_version.full == "3-A"
    comparison = result.inputs["comparison"]
    assert isinstance(comparison, dict) and comparison["task_version"] == "3-A"
    assert comparison["commit"] == result.subject.revision.commit


class _Spec:
    def __init__(self, registry: str | None, task: str) -> None:
        self.task_registry_name, self.task = registry, task


def test_qualified_registry_names_match_only_their_own_package() -> None:
    names = {"scicode"}
    assert _header_matches(_Spec("inspect_evals/scicode", "scicode"), "inspect_evals/scicode", names)
    # the sample auditor's task over scicode is not scicode
    assert not _header_matches(
        _Spec("audit/inspect_evals/scicode", "scicode"), "inspect_evals/scicode", names
    )
    # a bare task name (a Task run from a file) still matches on the tail
    assert _header_matches(_Spec(None, "scicode"), "inspect_evals/scicode", names)
    # a multi-task package: registry name is the task, package prefix still has to agree
    assert _header_matches(
        _Spec("inspect_evals/lab_bench_litqa", "lab_bench_litqa"),
        "inspect_evals/lab_bench",
        {"lab_bench", "lab_bench_litqa"},
    )
    assert not _header_matches(
        _Spec("other_pkg/lab_bench_litqa", "lab_bench_litqa"),
        "inspect_evals/lab_bench",
        {"lab_bench", "lab_bench_litqa"},
    )


def test_mock_logs_are_excluded_unless_the_filter_allows_them(tmp_path: Path) -> None:
    log = _log(tmp_path / "logs")
    headers = matching_headers([log], "inspect_evals/stereoset")
    used, excluded = select_headers(headers, LogFilter())
    assert used == [] and len(excluded) == 1
    assert excluded[0]["path"] == str(log) and "mockllm/model" in excluded[0]["reason"]
    used, excluded = select_headers(headers, LogFilter(include_mock=True))
    assert len(used) == 1 and excluded == []


def test_task_args_filter_excludes_other_configurations(tmp_path: Path) -> None:
    default = _log(tmp_path / "a")
    variant = _log(tmp_path / "b", task_args={"subset": "small"})
    headers = matching_headers([default, variant], "inspect_evals/stereoset")
    used, excluded = select_headers(headers, LogFilter(task_args={}, include_mock=True))
    assert [p.name for p, _ in used] == [default.name]
    assert excluded[0]["path"] == str(variant) and "task args" in excluded[0]["reason"]


def test_all_logs_excluded_is_a_skip_that_says_why(tmp_path: Path) -> None:
    root = make_root(tmp_path)
    log = _log(tmp_path / "logs")
    result = run("inspect_evals/stereoset", Context(ie_root=root, logs=[log]))  # default config
    assert [o.status for o in result.outcomes] == ["skip"]
    message = result.outcomes[0].message or ""
    assert "1 matching log" in message and "excluded" in message and "mockllm" in message


def test_non_default_task_args_skip_the_sample_count_comparison_but_join_drift(
    tmp_path: Path,
) -> None:
    root = make_root(tmp_path)  # eval.yaml declares 2123
    default = _log(tmp_path / "a", samples=3, version=3)
    variant = _log(tmp_path / "b", samples=2, version=4, task_args={"subset": "small"})
    result = run(
        "inspect_evals/stereoset", Context(ie_root=root, logs=[default, variant], config=MOCK_OK)
    )
    counts = [f for f in result.findings if f.rule == "header.dataset_samples"]
    assert len(counts) == 1 and "3" in counts[0].summary  # only the default-args log is compared
    assert any(f.rule == "header.version_drift" for f in result.findings)  # both logs join drift
    logs = result.inputs["logs"]
    assert isinstance(logs, dict)
    assert len(logs["used"]) == 2 and logs["excluded"] == []
    assert logs["count_excluded"] == [
        {
            "path": str(variant),
            "reason": "task args {'subset': 'small'} differ from the default configuration",
        }
    ]
