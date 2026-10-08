"""inspect-dataset scan output -> Run."""

import json
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError
from test_adapters_common import STUBS, make_root

from inspect_audit.findings.adapters import Context, _replay_samples, subject_for
from inspect_audit.findings.adapters.dataset import (
    DUMP_SCRIPT,
    PRODUCER,
    REPLAY_SCRIPT,
    SAMPLES_ENV,
    _scanner_outcomes,
    eval_dependency_args,
    hf_asset,
    parse,
    run,
    scan_arguments,
)
from inspect_audit.findings.config import Config, DatasetConfig, EvalConfig
from inspect_audit.findings.models import SampleLocation
from inspect_audit.findings.producers import ProducerConfig

FIXTURE = Path(__file__).parent / "fixtures" / "dataset"
# a real task-mode scan: StereoSet replayed from dumped samples, summary naming the replay spec
TASK_FIXTURE = Path(__file__).parent / "fixtures" / "dataset_task"
# a real 0.5.0 task-mode scan of BBH, with `scanner_status`
FIXTURE_0_5 = Path(__file__).parent / "fixtures" / "dataset_0_5"
STAMP = datetime(2026, 9, 25, 4, 20, 50, tzinfo=UTC)
ASSET = "external_assets:\n  - type: huggingface\n    source: McGill-NLP/stereoset\n    fetch_method: hf_dataset\n    state: pinned\n"


def test_parse_summary_outcomes_and_findings(tmp_path: Path) -> None:
    root = make_root(tmp_path, extra=ASSET)
    subject = subject_for("inspect_evals/stereoset", Context(ie_root=root))
    result = parse(FIXTURE, "inspect_evals/stereoset", subject, timestamp=STAMP)
    assert result.producer == PRODUCER
    assert result.subject.dataset is not None
    assert (
        result.subject.dataset.path,
        result.subject.dataset.config,
        result.subject.dataset.split,
    ) == ("McGill-NLP/stereoset", "intersentence", "validation")
    assert result.subject.dataset.revision is None
    assert sorted((o.rule, o.status) for o in result.outcomes) == [
        ("answer_length", "fail"),
        ("duplicate_questions", "fail"),
        ("inconsistent_format", "fail"),
    ]
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


def test_parse_0_5_takes_outcomes_from_scanner_status(tmp_path: Path) -> None:
    # `by_scanner` names only scanners with findings; `scanner_status`, new in 0.5.0, names every
    # scanner as ran or not_applicable. Without it a clean scan looks like one that examined nothing.
    root = make_root(tmp_path)
    subject = subject_for("inspect_evals/bbh", Context(ie_root=root))
    result = parse(FIXTURE_0_5, "inspect_evals/bbh", subject, timestamp=STAMP)
    assert result.producer_version == "0.5.0"
    statuses = {o.rule: o.status for o in result.outcomes}
    assert len(statuses) == 14
    assert {rule for rule, status in statuses.items() if status == "fail"} == {
        "binary_question_ratio",
        "duplicate_questions",
        "extraction_artifacts",
        "forced_choice_leakage",
    }
    assert [statuses[rule] for rule in ("answer_distribution", "mojibake")] == ["pass", "pass"]
    skipped = {o.rule: o.message for o in result.outcomes if o.status == "skip"}
    assert set(skipped) == {
        "answer_length",
        "image_mime_type",
        "inconsistent_format",
        "numeric_provenance",
        "text_layer_recall",
    }
    assert "no image field" in (skipped["image_mime_type"] or "")
    # forced_choice_leakage.json is absent from the fixture on size grounds
    assert len(result.findings) == 4 + 4 + 3


def test_a_scanner_with_findings_fails_whatever_its_status_says() -> None:
    outcomes = _scanner_outcomes(
        {"unlisted": {"total": 2}, "odd": {"total": 1}},
        {"odd": {"status": "not_applicable", "reason": "r"}},
    )
    assert [(o.rule, o.status) for o in outcomes] == [("odd", "fail"), ("unlisted", "fail")]


def test_hf_asset_reads_eval_yaml() -> None:
    assert hf_asset({"external_assets": [{"type": "huggingface", "source": "a/b"}]}) == "a/b"
    assert hf_asset({"external_assets": [{"type": "url", "source": "https://x"}]}) is None
    assert hf_asset({}) is None


def test_scan_arguments_prefer_the_declaration() -> None:
    declared = DatasetConfig(
        path="McGill-NLP/stereoset",
        config="intersentence",
        split="validation",
        revision="abc123",
        fields={"question": "context", "answer": "sentences", "id": "id"},
    )
    path, options, examined = scan_arguments("inspect_evals/stereoset", {}, declared)
    assert path == "McGill-NLP/stereoset"
    assert options == [
        "--config",
        "intersentence",
        "--split",
        "validation",
        "--revision",
        "abc123",
        "--question-field",
        "context",
        "--answer-field",
        "sentences",
        "--id-field",
        "id",
    ]
    assert examined == {
        "path": "McGill-NLP/stereoset",
        "config": "intersentence",
        "split": "validation",
        "revision": "abc123",
        "fields": {"question": "context", "answer": "sentences", "id": "id"},
        "declared": True,
        "mode": "hf",
        "task": None,
    }


def test_scan_arguments_fall_back_to_eval_yaml_asset() -> None:
    yaml_data = {"external_assets": [{"type": "huggingface", "source": "a/b"}]}
    path, options, examined = scan_arguments("inspect_evals/x", yaml_data, None)
    assert (path, options) == ("a/b", [])
    assert examined["declared"] is False and examined["path"] == "a/b"


def test_scan_arguments_declared_without_path_uses_asset_for_path_only() -> None:
    yaml_data = {"external_assets": [{"type": "huggingface", "source": "a/b"}]}
    declared = DatasetConfig(split="test")
    path, options, examined = scan_arguments("inspect_evals/x", yaml_data, declared)
    assert path == "a/b" and options == ["--split", "test"]
    assert examined["declared"] is True and examined["path"] == "a/b"


def test_run_without_a_declaration_asset_or_task_is_a_skip_naming_all_three(
    tmp_path: Path,
) -> None:
    root = make_root(tmp_path)
    (root / "src" / "inspect_evals" / "stereoset" / "eval.yaml").write_text("title: StereoSet\n")
    result = run("inspect_evals/stereoset", Context(ie_root=root))
    assert result.outcomes[0].status == "skip"
    message = result.outcomes[0].message or ""
    assert "pilot config" in message and "eval.yaml" in message and "tasks" in message


def test_run_with_a_stubbed_scan(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = make_root(tmp_path, extra=ASSET)
    monkeypatch.setenv("STUB_OUTPUT_DIR", str(FIXTURE))
    declared = DatasetConfig(
        config="intersentence",
        split="validation",
        fields={"question": "context", "answer": "sentences", "id": "id"},
    )
    ctx = Context(
        ie_root=root,
        out_dir=tmp_path / "out",
        producers=ProducerConfig(dataset=(sys.executable, str(STUBS / "echo_file.py"))),
        config=Config(evals={"inspect_evals/stereoset": EvalConfig(dataset=declared)}),
    )
    result = run("inspect_evals/stereoset", ctx)
    assert len(result.findings) == 46
    argv = result.inputs["argv"]
    assert isinstance(argv, list)
    assert "McGill-NLP/stereoset" in argv and "--config" in argv and "--question-field" in argv
    examined = result.inputs["dataset"]
    assert isinstance(examined, dict)
    assert examined["declared"] is True and examined["split"] == "validation"


def test_run_with_a_failing_scan_is_a_skip(tmp_path: Path) -> None:
    root = make_root(tmp_path, extra=ASSET)
    result = run("inspect_evals/stereoset", _task_ctx(root, tmp_path, scan="fail.py"))
    assert result.outcomes[0].status == "skip"
    assert "boom" in (result.outcomes[0].message or "")


def test_run_with_malformed_scan_is_a_skip(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = make_root(tmp_path, extra=ASSET)
    bad = tmp_path / "badscan"
    bad.mkdir()
    (bad / "scan_summary.json").write_text("[]")  # valid JSON, wrong shape
    monkeypatch.setenv("STUB_OUTPUT_DIR", str(bad))
    result = run("inspect_evals/stereoset", _task_ctx(root, tmp_path))
    assert result.findings == []
    assert result.outcomes[0].status == "skip"
    assert "could not parse" in (result.outcomes[0].message or "")


TASKS = {
    "tasks": [{"name": "arc_easy"}, {"name": "arc_challenge"}],
    "external_assets": [{"type": "huggingface", "source": "allenai/ai2_arc"}],
}


def test_scan_arguments_default_to_the_first_task() -> None:
    path, options, examined = scan_arguments("inspect_evals/arc", TASKS, None)
    assert (path, options) == ("inspect_evals/arc_easy", [])
    assert examined == {
        "path": "inspect_evals/arc_easy",
        "config": None,
        "split": None,
        "revision": None,
        "fields": {},
        "declared": False,
        "mode": "task",
        "task": "arc_easy",
    }


def test_scan_arguments_declared_task_is_scanned() -> None:
    path, options, examined = scan_arguments(
        "inspect_evals/arc", TASKS, DatasetConfig(task="arc_challenge")
    )
    assert (path, options) == ("inspect_evals/arc_challenge", [])
    assert examined["mode"] == "task" and examined["declared"] is True


def test_scan_arguments_hf_declaration_wins_over_tasks() -> None:
    path, options, examined = scan_arguments(
        "inspect_evals/arc", TASKS, DatasetConfig(config="ARC-Easy", split="test")
    )
    assert path == "allenai/ai2_arc" and options == ["--config", "ARC-Easy", "--split", "test"]
    assert examined["mode"] == "hf" and examined["task"] is None


def test_declaring_a_task_with_hf_settings_is_rejected() -> None:
    with pytest.raises(ValidationError, match="cannot be combined"):
        DatasetConfig(task="arc_easy", split="test")


def _task_ctx(
    root: Path, tmp_path: Path, *, scan: str = "echo_file.py", dump: str = "dump_samples.py"
) -> Context:
    """A context whose task scans run stubs: the dump stub gets the same arguments as `uv run` would."""
    return Context(
        ie_root=root,
        out_dir=tmp_path / "out",
        producers=ProducerConfig(
            dataset=(sys.executable, str(STUBS / "fail.py")),
            dataset_dump=(
                sys.executable,
                str(STUBS / dump),
                "--project",
                "{ie_root}",
                "{eval_deps}",
            ),
            dataset_task=(sys.executable, str(STUBS / scan), "{inspect_ai}"),
            eval_env=str(tmp_path / "eval-env"),
        ),
    )


def test_task_scan_dumps_in_the_eval_environment_then_scans_the_replay(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = make_root(tmp_path, extra=ASSET)
    (root / "pyproject.toml").write_text(
        "[project.optional-dependencies]\nstereoset = []\n[dependency-groups]\nstereoset = []\n"
    )
    dump_record, scan_record = tmp_path / "dump.json", tmp_path / "scan.json"
    monkeypatch.setenv("STUB_RECORD", str(dump_record))
    monkeypatch.setenv("STUB_SCAN_RECORD", str(scan_record))
    monkeypatch.setenv("STUB_OUTPUT_DIR", str(TASK_FIXTURE))
    result = run("inspect_evals/stereoset", _task_ctx(root, tmp_path))
    assert len(result.findings) == 18

    dump = json.loads(dump_record.read_text())
    assert dump["argv"][1:] == [
        "--project",
        str(root),
        "--extra",
        "stereoset",
        "--group",
        "stereoset",
        str(DUMP_SCRIPT),
        "inspect_evals/stereoset",
        dump["argv"][-2],
        dump["argv"][-1],
    ]
    assert Path(dump["cwd"]).resolve() == root.resolve()
    assert dump["env"] == str(tmp_path / "eval-env")
    assert dump["bytecode"] == "1"

    scan = json.loads(scan_record.read_text())
    assert scan["argv"][1:3] == ["scan", f"{REPLAY_SCRIPT}@replay_samples"]  # no uv.lock: no pin
    assert scan["samples"] == dump["argv"][-2]
    assert scan["scorers"] == "inspect_ai/exact"  # the eval's scorers reach the replay task
    dataset_inputs = result.inputs["dataset"]
    assert isinstance(dataset_inputs, dict) and dataset_inputs["scorers"] == ["inspect_ai/exact"]
    assert Path(scan["cwd"]).resolve() == Path(str(result.inputs["scan_dir"])).resolve()
    dump_argv = result.inputs["dump_argv"]
    assert isinstance(dump_argv, list) and dump_argv[1:] == dump["argv"]  # [0] is the interpreter


def test_task_scan_records_the_dataset_inspect_loads_not_the_first_asset(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # eval.yaml's first asset is another dataset, as with MMLU (openai/MMMLU listed, cais/mmlu loaded)
    other = ASSET.replace("McGill-NLP/stereoset", "someone/translations")
    root = make_root(tmp_path, extra=other)
    monkeypatch.setenv("STUB_OUTPUT_DIR", str(TASK_FIXTURE))
    result = run("inspect_evals/stereoset", _task_ctx(root, tmp_path))
    # the fixture summary names the replay spec and a train split; neither describes the eval's dataset
    assert result.subject.dataset is not None
    assert result.subject.dataset.model_dump() == {
        "path": "McGill-NLP/stereoset",
        "config": None,
        "split": None,
        "revision": None,
    }
    primary = result.findings[0].primary_location
    assert isinstance(primary, SampleLocation) and primary.dataset == "McGill-NLP/stereoset"
    examined = result.inputs["dataset"]
    assert isinstance(examined, dict)
    assert (examined["mode"], examined["task"], examined["samples"]) == ("task", "stereoset", 2123)


def test_task_scan_without_a_dataset_name_uses_the_task_spec(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = make_root(tmp_path)
    monkeypatch.setenv("STUB_OUTPUT_DIR", str(TASK_FIXTURE))
    monkeypatch.setenv("STUB_META", json.dumps({"dataset_name": None, "dataset_location": None}))
    result = run("inspect_evals/stereoset", _task_ctx(root, tmp_path))
    assert result.subject.dataset is not None
    assert result.subject.dataset.path == "inspect_evals/stereoset"


def test_a_failing_dump_is_a_skip_naming_the_dump(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = make_root(tmp_path, extra=ASSET)
    monkeypatch.setenv("STUB_DUMP_EXIT", "1")
    result = run("inspect_evals/stereoset", _task_ctx(root, tmp_path))
    assert result.outcomes[0].status == "skip"
    message = result.outcomes[0].message or ""
    assert "sample dump exit 1" in message and "dump boom" in message


def test_the_default_eval_environment_is_a_cache_directory_per_checkout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = make_root(tmp_path, extra=ASSET)
    record = tmp_path / "dump.json"
    monkeypatch.setenv("STUB_RECORD", str(record))
    monkeypatch.setenv("STUB_OUTPUT_DIR", str(TASK_FIXTURE))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    ctx = _task_ctx(root, tmp_path)
    ctx = Context(
        ie_root=root,
        out_dir=ctx.out_dir,
        producers=ProducerConfig(
            dataset_dump=ctx.producers.dataset_dump, dataset_task=ctx.producers.dataset_task
        ),
    )
    run("inspect_evals/stereoset", ctx)
    env = Path(json.loads(record.read_text())["env"])
    assert env.parent == tmp_path / "cache" / "inspect_audit" / "eval-envs"
    assert env.name.startswith("ie-")


def test_eval_dependency_args_name_the_evals_extra_and_group(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text(
        "[project.optional-dependencies]\nifeval = []\nother = []\n"
        "[dependency-groups]\nifeval = []\n"
    )
    assert eval_dependency_args(tmp_path, "ifeval") == ["--extra", "ifeval", "--group", "ifeval"]
    assert eval_dependency_args(tmp_path, "other") == ["--extra", "other"]
    assert eval_dependency_args(tmp_path, "stereoset") == []
    assert eval_dependency_args(tmp_path / "missing", "ifeval") == []


def test_replay_task_reads_the_samples_file_the_adapter_names() -> None:
    assert _replay_samples.SAMPLES_ENV == SAMPLES_ENV


@pytest.mark.parametrize(
    "task", ["", "inspect_evals/arc_easy", "arc.py@arc_easy", " arc_easy", "arc-easy", "1arc"]
)
def test_a_task_must_be_a_bare_task_name(task: str) -> None:
    with pytest.raises(ValidationError, match="bare task name"):
        DatasetConfig(task=task)


@pytest.mark.parametrize("task", ["arc_easy", "sad_mini", "mmlu_0_shot", "_private"])
def test_a_task_name_is_a_python_identifier(task: str) -> None:
    assert DatasetConfig(task=task).task == task


def test_a_declared_task_missing_from_eval_yaml_is_a_skip_naming_the_listed_tasks(
    tmp_path: Path,
) -> None:
    root = make_root(tmp_path, extra=ASSET)
    ctx = _task_ctx(root, tmp_path)
    ctx = Context(
        ie_root=root,
        out_dir=ctx.out_dir,
        producers=ctx.producers,
        config=Config(
            evals={"inspect_evals/stereoset": EvalConfig(dataset=DatasetConfig(task="stereo"))}
        ),
    )
    result = run("inspect_evals/stereoset", ctx)
    assert result.outcomes[0].status == "skip"
    message = result.outcomes[0].message or ""
    assert "'stereo'" in message and "stereoset" in message


def test_hf_settings_without_a_path_is_a_skip_that_says_so(tmp_path: Path) -> None:
    root = make_root(tmp_path)  # eval.yaml lists a task but has no huggingface asset
    ctx = Context(
        ie_root=root,
        config=Config(
            evals={"inspect_evals/stereoset": EvalConfig(dataset=DatasetConfig(split="test"))}
        ),
    )
    result = run("inspect_evals/stereoset", ctx)
    assert result.outcomes[0].status == "skip"
    message = result.outcomes[0].message or ""
    assert "HuggingFace settings" in message and "no tasks" not in message


@pytest.mark.parametrize(
    ("meta", "identity"),
    [
        # a HuggingFace dataset: the location is the hub path, preferred over a differing name
        ({"dataset_name": "mmlu", "dataset_location": "cais/mmlu"}, "cais/mmlu"),
        # a downloaded file: the location is this machine's cache path and the name only its stem
        (
            {
                "dataset_name": "gpqa_diamond",
                "dataset_location": "/Users/someone/Library/Caches/inspect_evals/gpqa/gpqa_diamond.csv",
            },
            "inspect_evals/stereoset",
        ),
        (
            {"dataset_name": "x", "dataset_location": "file:///home/someone/x.jsonl"},
            "inspect_evals/stereoset",
        ),
        ({"dataset_name": "x", "dataset_location": "~/data/x.csv"}, "inspect_evals/stereoset"),
        ({"dataset_name": "x", "dataset_location": "C:\\data\\x.csv"}, "inspect_evals/stereoset"),
    ],
)
def test_task_scan_identity_is_never_a_local_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, meta: dict[str, str], identity: str
) -> None:
    root = make_root(tmp_path, extra=ASSET)
    monkeypatch.setenv("STUB_OUTPUT_DIR", str(TASK_FIXTURE))
    monkeypatch.setenv("STUB_META", json.dumps(meta))
    result = run("inspect_evals/stereoset", _task_ctx(root, tmp_path))
    assert result.subject.dataset is not None and result.subject.dataset.path == identity
    primary = result.findings[0].primary_location
    assert isinstance(primary, SampleLocation) and primary.dataset == identity


def test_an_unreadable_pyproject_is_a_skip_not_a_crash(tmp_path: Path) -> None:
    root = make_root(tmp_path, extra=ASSET)
    (root / "pyproject.toml").write_text("[project\nname=")
    result = run("inspect_evals/stereoset", _task_ctx(root, tmp_path))
    assert result.outcomes[0].status == "skip"
    assert "pyproject.toml" in (result.outcomes[0].message or "")


@pytest.mark.parametrize(
    ("env", "expected"),
    [({"STUB_META": "not json"}, "unreadable"), ({"STUB_NO_META": "1"}, "sample dump exit 0")],
)
def test_a_dump_without_readable_meta_is_a_skip(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, env: dict[str, str], expected: str
) -> None:
    root = make_root(tmp_path, extra=ASSET)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    result = run("inspect_evals/stereoset", _task_ctx(root, tmp_path))
    assert result.outcomes[0].status == "skip"
    assert expected in (result.outcomes[0].message or "")


def test_a_failed_dump_leaves_no_scan_dir_and_records_the_dump(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = make_root(tmp_path, extra=ASSET)
    monkeypatch.setenv("STUB_DUMP_EXIT", "1")
    ctx = _task_ctx(root, tmp_path)
    result = run("inspect_evals/stereoset", ctx)
    assert result.outcomes[0].status == "skip"
    assert not list(ctx.out_dir.glob("inspect_dataset_*"))
    dump_argv = result.inputs["dump_argv"]
    assert isinstance(dump_argv, list) and "inspect_evals/stereoset" in dump_argv


LOCK = """version = 1

[[package]]
name = "inspect-ai"
version = "0.3.263"

[[package]]
name = "datasets"
version = "4.8.5"
"""


def test_the_replay_uses_the_inspect_ai_the_eval_locks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = make_root(tmp_path, extra=ASSET)
    (root / "uv.lock").write_text(LOCK)
    record = tmp_path / "scan.json"
    monkeypatch.setenv("STUB_SCAN_RECORD", str(record))
    monkeypatch.setenv("STUB_OUTPUT_DIR", str(TASK_FIXTURE))
    result = run("inspect_evals/stereoset", _task_ctx(root, tmp_path))
    argv = json.loads(record.read_text())["argv"]
    assert argv[1:3] == ["--with", "inspect-ai==0.3.263"]
    examined = result.inputs["dataset"]
    assert isinstance(examined, dict) and examined["inspect_ai"] == "0.3.263"


def test_without_a_lock_the_replay_is_not_pinned(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = make_root(tmp_path, extra=ASSET)
    record = tmp_path / "scan.json"
    monkeypatch.setenv("STUB_SCAN_RECORD", str(record))
    monkeypatch.setenv("STUB_OUTPUT_DIR", str(TASK_FIXTURE))
    result = run("inspect_evals/stereoset", _task_ctx(root, tmp_path))
    assert json.loads(record.read_text())["argv"][1] == "scan"
    examined = result.inputs["dataset"]
    assert isinstance(examined, dict) and examined["inspect_ai"] is None


def test_an_hf_scan_runs_from_the_callers_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # inspect-dataset reads ./.env (an HF token for gated datasets, say) and resolves relative paths from it
    caller = tmp_path / "caller"
    caller.mkdir()
    monkeypatch.chdir(caller)
    root = make_root(tmp_path, extra=ASSET)
    record = tmp_path / "scan.json"
    monkeypatch.setenv("STUB_SCAN_RECORD", str(record))
    monkeypatch.setenv("STUB_OUTPUT_DIR", str(FIXTURE))
    ctx = Context(
        ie_root=root,
        out_dir=tmp_path / "out",
        producers=ProducerConfig(dataset=(sys.executable, str(STUBS / "echo_file.py"))),
        config=Config(
            evals={"inspect_evals/stereoset": EvalConfig(dataset=DatasetConfig(split="validation"))}
        ),
    )
    run("inspect_evals/stereoset", ctx)
    assert Path(json.loads(record.read_text())["cwd"]).resolve() == caller.resolve()


# A question long enough that a whole copy of it in a finding is unmistakable.
LONG = "A 67-year-old patient presents with fatigue, joint pain and a rash across both cheeks"
LONG_ID = "live_irrelevance_835-326-0-with-a-long-suffix"


def _scan(tmp_path: Path, rows: dict[str, list[dict[str, object]]]) -> Path:
    scan_dir = tmp_path / "scan"
    scan_dir.mkdir()
    summary = {
        "version": "0.5.0",
        "dataset_name": "cais/hle",
        "by_scanner": {name: {"total": len(found)} for name, found in rows.items()},
        "scanner_status": {name: {"status": "ran"} for name in rows},
    }
    (scan_dir / "scan_summary.json").write_text(json.dumps(summary))
    for name, found in rows.items():
        (scan_dir / f"{name}.json").write_text(json.dumps(found))
    return scan_dir


def _parsed(tmp_path: Path, rows: dict[str, list[dict[str, object]]]):
    root = make_root(tmp_path)
    subject = subject_for("inspect_evals/hle", Context(ie_root=root))
    return parse(_scan(tmp_path, rows), "inspect_evals/hle", subject, timestamp=STAMP).findings


def test_sample_text_is_cut_to_a_marker_in_the_summary_and_the_record(tmp_path: Path) -> None:
    explanation = (
        "The question contains non-printable character(s) '\\t'. These are likely data entry "
        f"errors and may cause silent failures in downstream processing. Value: {LONG!r}"
    )
    (finding,) = _parsed(
        tmp_path,
        {
            "encoding_issues": [
                {
                    "scanner": "encoding_issues",
                    "severity": "low",
                    "explanation": explanation,
                    "sample_id": "hle_4d1822dc",
                    "metadata": {"field": "question", "bad_chars": ["'\\t'"], "value": LONG},
                }
            ]
        },
    )
    assert LONG not in finding.summary
    assert LONG not in json.dumps(finding.source.record)
    assert f"Value: '{LONG[:32]}…'" in finding.summary
    # short literals are the scanner's own words, kept as they are
    assert "character(s) '\\t'." in finding.summary
    record = finding.source.record
    assert isinstance(record, dict) and isinstance(record["metadata"], dict)
    assert record["metadata"]["value"] == f"{LONG[:32]}…"
    assert record["metadata"]["field"] == "question"
    assert record["sample_id"] == "hle_4d1822dc"


def test_a_question_is_cut_and_a_short_answer_kept(tmp_path: Path) -> None:
    answer = {"a": "True"}
    explanation = (
        "Question explicitly offers the answer as one of its options ('...or...'). "
        f"Question: {LONG!r}  Answer: {str(answer)!r}"
    )
    (finding,) = _parsed(
        tmp_path,
        {
            "forced_choice_leakage": [
                {
                    "scanner": "forced_choice_leakage",
                    "severity": "medium",
                    "explanation": explanation,
                    "sample_id": "hle_1",
                    "metadata": {"question": LONG, "answer": str(answer), "options": [LONG, "no"]},
                }
            ]
        },
    )
    record = json.dumps(finding.source.record)
    assert LONG not in finding.summary and LONG not in record
    assert "('...or...')" in finding.summary
    assert "Answer: \"{'a': 'True'}\"" in finding.summary


def test_sample_ids_and_indices_are_kept_whole(tmp_path: Path) -> None:
    (finding,) = _parsed(
        tmp_path,
        {
            "duplicate_questions": [
                {
                    "scanner": "duplicate_questions",
                    "severity": "high",
                    "explanation": "2 samples share the question, with the same answer '' (at indices [3, 9]).",
                    "sample_id": LONG_ID,
                    "metadata": {
                        "question": LONG,
                        "duplicate_ids": [LONG_ID, "short_id"],
                        "duplicate_indices": [3, 9],
                    },
                }
            ]
        },
    )
    record = finding.source.record
    assert isinstance(record, dict) and isinstance(record["metadata"], dict)
    assert record["metadata"]["duplicate_ids"] == [LONG_ID, "short_id"]
    assert record["metadata"]["duplicate_indices"] == [3, 9]
    assert record["sample_id"] == LONG_ID
    assert LONG not in json.dumps(record)


def test_an_apostrophe_in_the_scanners_prose_does_not_open_a_quote(tmp_path: Path) -> None:
    explanation = f"The sample's question repeats another's verbatim: {LONG!r}"
    (finding,) = _parsed(
        tmp_path,
        {
            "duplicate_questions": [
                {
                    "scanner": "duplicate_questions",
                    "severity": "low",
                    "explanation": explanation,
                    "sample_id": "s1",
                }
            ]
        },
    )
    assert finding.summary == f"The sample's question repeats another's verbatim: '{LONG[:32]}…'"


TRACEBACK = """Traceback (most recent call last):
  File "/x/inspect_ai/model/_model.py", line 1, in get_model
    raise pip_dependency_error(FEATURE, [PACKAGE])
inspect_ai._util.error.PrerequisiteError: [bold]ERROR[/bold]: OpenAI API requires optional dependencies. Install with:

[bold]pip install openai[/bold]
"""


def test_a_failure_reason_is_the_exception_line_without_markup() -> None:
    from inspect_audit.findings.adapters.dataset import failure_reason

    assert failure_reason(TRACEBACK) == (
        "PrerequisiteError: ERROR: OpenAI API requires optional dependencies. Install with:"
    )


def test_a_failure_reason_keeps_brackets_that_are_not_markup() -> None:
    from inspect_audit.findings.adapters.dataset import failure_reason

    text = "rich.errors.MarkupError: closing tag '[/ANSWER]' at position 1067 doesn't match\n"
    assert (
        failure_reason(text)
        == "MarkupError: closing tag '[/ANSWER]' at position 1067 doesn't match"
    )


def test_a_failure_reason_without_an_exception_is_the_last_line() -> None:
    from inspect_audit.findings.adapters.dataset import failure_reason

    assert failure_reason("loading...\nSet CYBENCH_ACKNOWLEDGE_RISKS=1 to proceed.\n\n") == (
        "Set CYBENCH_ACKNOWLEDGE_RISKS=1 to proceed."
    )
