"""End to end over a temporary inspect_evals root with stubbed producers."""

import json
import sys
from pathlib import Path

import pandas as pd
import pytest
from test_adapters_common import STUBS, make_root
from test_header_adapter import _log

from inspect_audit.findings.cli import collect_logs, main
from inspect_audit.findings.featured import FEATURED
from inspect_audit.findings.models import Run
from inspect_audit.findings.producers import ProducerConfig

FIXTURES = Path(__file__).parent / "fixtures"
PILOT = FIXTURES / "pilot.yaml"  # allows the mock-model logs the tests produce
ASSET = "external_assets:\n  - type: huggingface\n    source: McGill-NLP/stereoset\n    fetch_method: hf_dataset\n    state: pinned\n"


def _stubbed_env(monkeypatch: pytest.MonkeyPatch, *, lint_ok: bool = True) -> None:
    lint = str(STUBS / ("echo_file.py" if lint_ok else "fail.py"))
    monkeypatch.setenv("INSPECT_AUDIT_LINT_CMD", f"{sys.executable} {lint}")
    monkeypatch.setenv("INSPECT_AUDIT_DATASET_CMD", f"{sys.executable} {STUBS / 'echo_file.py'}")
    monkeypatch.setenv(
        "INSPECT_AUDIT_DATASET_TASK_CMD", f"{sys.executable} {STUBS / 'echo_file.py'}"
    )
    monkeypatch.setenv(
        "INSPECT_AUDIT_DATASET_DUMP_CMD", f"{sys.executable} {STUBS / 'dump_samples.py'}"
    )
    monkeypatch.setenv("STUB_OUTPUT_FILE", str(FIXTURES / "lint.json"))
    monkeypatch.setenv("STUB_OUTPUT_DIR", str(FIXTURES / "dataset"))


def test_featured_has_35_ids() -> None:
    assert len(FEATURED) == 35 and len(set(FEATURED)) == 35 and "stereoset" not in FEATURED


def test_collect_logs_lists_directories_and_files(tmp_path: Path) -> None:
    a = _log(tmp_path / "a")
    b = _log(tmp_path / "b" / "nested")
    assert sorted(p.name for p in collect_logs([str(tmp_path / "a"), str(b)])) == sorted(
        [a.name, b.name]
    )


def test_run_writes_the_layout(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = make_root(tmp_path, extra=ASSET)
    log = _log(tmp_path / "logs", samples=3)
    _stubbed_env(monkeypatch)
    out = tmp_path / "out"
    code = main(
        [
            "run",
            "--config",
            str(PILOT),
            "--root",
            str(root),
            "--logs",
            str(log),
            "--out",
            str(out),
            "inspect_evals/stereoset",
        ]
    )
    assert code == 0
    slug_dir = out / "inspect-evals-stereoset"
    run_files = sorted(p.name for p in (slug_dir / "runs").glob("*.run.json"))
    assert len(run_files) == 3
    assert [name.split("-")[0] for name in run_files] == [
        "inspect_audit_header",
        "inspect_dataset",
        "inspect_evals_lint",
    ]
    current = json.loads((slug_dir / "current.json").read_text())
    assert sorted(current) == ["inspect_audit_header", "inspect_dataset", "inspect_evals_lint"]
    assert all((slug_dir / rel).is_file() for rel in current.values())
    assert (slug_dir / "SUMMARY.md").read_text().startswith("# inspect_evals/stereoset")
    assert (out / "SUMMARY.md").read_text().startswith("# Sweep summary")
    findings = pd.read_parquet(out / "findings.parquet")
    assert len(findings) == 1 + 46 + 1  # lint, dataset, header.dataset_samples (3 != 2123)
    runs = pd.read_parquet(out / "runs.parquet")
    assert len(runs) == 3 and not runs["skipped"].any()


def test_run_with_a_failing_producer_exits_one_and_records_a_skip(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = make_root(tmp_path, extra=ASSET)
    log = _log(tmp_path / "logs")
    _stubbed_env(monkeypatch, lint_ok=False)
    out = tmp_path / "out"
    code = main(
        [
            "run",
            "--config",
            str(PILOT),
            "--root",
            str(root),
            "--logs",
            str(log),
            "--out",
            str(out),
            "inspect_evals/stereoset",
        ]
    )
    assert code == 1
    runs = pd.read_parquet(out / "runs.parquet")
    assert runs.loc[runs["producer"] == "inspect_evals_lint", "skipped"].item()


def test_producers_flag_selects_external_producers_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = make_root(tmp_path, extra=ASSET)
    log = _log(tmp_path / "logs")
    _stubbed_env(monkeypatch)
    out = tmp_path / "out"
    assert (
        main(
            [
                "run",
                "--config",
                str(PILOT),
                "--root",
                str(root),
                "--logs",
                str(log),
                "--out",
                str(out),
                "--producers",
                "lint",
                "inspect_evals/stereoset",
            ]
        )
        == 0
    )
    run_files = sorted((out / "inspect-evals-stereoset" / "runs").glob("*.run.json"))
    assert [p.name.split("-")[0] for p in run_files] == [
        "inspect_audit_header",
        "inspect_evals_lint",
    ]


def test_summary_regenerates_identical_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = make_root(tmp_path, extra=ASSET)
    log = _log(tmp_path / "logs")
    _stubbed_env(monkeypatch)
    out = tmp_path / "out"
    main(
        [
            "run",
            "--config",
            str(PILOT),
            "--root",
            str(root),
            "--logs",
            str(log),
            "--out",
            str(out),
            "inspect_evals/stereoset",
        ]
    )
    before = {p: p.read_text() for p in out.rglob("SUMMARY.md")}
    for path in before:
        path.write_text("stale")
    assert main(["summary", str(out)]) == 0
    assert {p: p.read_text() for p in out.rglob("SUMMARY.md")} == before


def test_no_targets_is_a_usage_error(tmp_path: Path) -> None:
    assert main(["run", "--root", str(tmp_path), "--out", str(tmp_path / "o")]) == 2


def test_featured_flag_expands(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from inspect_audit.findings import cli

    seen: list[list[str]] = []
    monkeypatch.setattr(
        cli, "sweep", lambda targets, ctx, producers, **kw: seen.append(list(targets)) or {}
    )
    monkeypatch.setattr(cli, "write_outputs", lambda out, runs, review=None: None)
    assert main(["run", "--root", str(tmp_path), "--out", str(tmp_path / "o"), "--featured"]) == 0
    assert seen[0] == [f"inspect_evals/{name}" for name in FEATURED]


def test_without_logs_the_header_producer_is_not_run_and_exit_is_zero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = make_root(tmp_path, extra=ASSET)
    _stubbed_env(monkeypatch)
    out = tmp_path / "out"
    assert (
        main(
            [
                "run",
                "--config",
                str(PILOT),
                "--root",
                str(root),
                "--out",
                str(out),
                "--producers",
                "lint",
                "inspect_evals/stereoset",
            ]
        )
        == 0
    )
    run_files = list((out / "inspect-evals-stereoset" / "runs").glob("*.run.json"))
    assert [p.name.split("-")[0] for p in run_files] == ["inspect_evals_lint"]


def test_hawk_logs_source_is_downloaded_into_the_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from test_hawk import STUB as HAWK_STUB

    src = _log(tmp_path / "src", samples=3)
    monkeypatch.setenv("STUB_EVAL_SRC", str(src))
    monkeypatch.setenv("INSPECT_AUDIT_HAWK_CMD", f"{sys.executable} {HAWK_STUB}")
    cache = tmp_path / "hawkcache"
    paths = collect_logs(["hawk:scicode-a"], hawk_cache=cache, producers=ProducerConfig.from_env())
    assert paths == [cache / "scicode-a" / src.name]


def test_hawk_task_flag_resolves_sets_by_task(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from inspect_audit.findings import cli, hawk
    from inspect_audit.findings.hawk import EvalSetInfo

    found = [
        EvalSetInfo(
            eval_set_id="scicode-a",
            created_at="2026-09-24T08:46:40Z",
            created_by="u1",
            eval_count=9,
            task_names=["inspect_evals/scicode"],
        )
    ]
    monkeypatch.setattr(hawk, "find_eval_sets", lambda task, **kw: found)
    seen: dict[str, object] = {}
    monkeypatch.setattr(
        cli, "collect_logs", lambda sources, **kw: seen.setdefault("sources", list(sources)) and []
    )
    monkeypatch.setattr(cli, "sweep", lambda targets, ctx, producers, **kw: {})
    monkeypatch.setattr(cli, "write_outputs", lambda out, runs, review=None: None)
    assert (
        main(
            [
                "run",
                "--config",
                str(PILOT),
                "--root",
                str(tmp_path),
                "--out",
                str(tmp_path / "o"),
                "--hawk-task",
                "inspect_evals/scicode",
                "inspect_evals/scicode",
            ]
        )
        == 0
    )
    assert seen["sources"] == ["hawk:scicode-a"]


def test_hawk_task_flag_refuses_too_many_sets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from inspect_audit.findings import hawk
    from inspect_audit.findings.hawk import EvalSetInfo

    many = [
        EvalSetInfo(
            eval_set_id=f"s{i}",
            created_at="2026-09-24T00:00:00Z",
            created_by="u",
            eval_count=1,
            task_names=["inspect_evals/scicode"],
        )
        for i in range(25)
    ]
    monkeypatch.setattr(hawk, "find_eval_sets", lambda task, **kw: many)
    assert (
        main(
            [
                "run",
                "--config",
                str(PILOT),
                "--root",
                str(tmp_path),
                "--out",
                str(tmp_path / "o"),
                "--hawk-task",
                "inspect_evals/scicode",
                "inspect_evals/scicode",
            ]
        )
        == 2
    )


def test_hawk_sets_subcommand_lists_matches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from inspect_audit.findings import hawk
    from inspect_audit.findings.hawk import EvalSetInfo

    found = [
        EvalSetInfo(
            eval_set_id="scicode-a",
            created_at="2026-09-24T08:46:40Z",
            created_by="u1",
            eval_count=9,
            task_names=["inspect_evals/scicode"],
        )
    ]
    monkeypatch.setattr(hawk, "find_eval_sets", lambda task, **kw: found)
    assert main(["hawk-sets", "inspect_evals/scicode"]) == 0
    out = capsys.readouterr().out
    assert "scicode-a" in out and "9" in out


def test_hawk_pull_uses_the_manifest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from test_hawk import STUB as HAWK_STUB

    src = _log(tmp_path / "src")
    monkeypatch.setenv("STUB_EVAL_SRC", str(src))
    monkeypatch.setenv("INSPECT_AUDIT_HAWK_CMD", f"{sys.executable} {HAWK_STUB}")
    manifest = tmp_path / "m.yaml"
    manifest.write_text(
        f"dest: {tmp_path / 'dest'}\nlogs:\n  - id: set-a\nartifacts:\n  - id: inv-1\n"
    )
    assert main(["hawk-pull", "--manifest", str(manifest)]) == 0
    out = capsys.readouterr().out
    assert "set-a" in out and "inv-1" in out
    assert (tmp_path / "dest" / "logs" / "set-a" / src.name).is_file()
    assert (tmp_path / "dest" / "artifacts" / "inv-1" / "bundle.txt").is_file()


def test_reruns_keep_earlier_runs_and_a_partial_sweep_keeps_other_producers_current(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = make_root(tmp_path, extra=ASSET)
    log = _log(tmp_path / "logs")
    _stubbed_env(monkeypatch)
    out = tmp_path / "out"
    args = [
        "run",
        "--config",
        str(PILOT),
        "--root",
        str(root),
        "--logs",
        str(log),
        "--out",
        str(out),
    ]
    assert main([*args, "inspect_evals/stereoset"]) == 0
    slug_dir = out / "inspect-evals-stereoset"
    first = json.loads((slug_dir / "current.json").read_text())
    # second sweep, lint only, no logs: the first three run files survive, only lint's pointer moves
    assert (
        main(
            [
                "run",
                "--config",
                str(PILOT),
                "--root",
                str(root),
                "--out",
                str(out),
                "--producers",
                "lint",
                "inspect_evals/stereoset",
            ]
        )
        == 0
    )
    second = json.loads((slug_dir / "current.json").read_text())
    assert len(list((slug_dir / "runs").glob("*.run.json"))) == 4
    assert second["inspect_dataset"] == first["inspect_dataset"]
    assert second["inspect_audit_header"] == first["inspect_audit_header"]
    assert second["inspect_evals_lint"] != first["inspect_evals_lint"]
    runs = pd.read_parquet(out / "runs.parquet")
    assert len(runs) == 3  # the current view, not every run ever written
    assert set(runs["run_id"]) == {
        Path(rel).name.removesuffix(".run.json") for rel in second.values()
    }
    assert "header.dataset_samples" in (slug_dir / "SUMMARY.md").read_text()


def test_default_config_excludes_mock_logs_so_header_is_a_skip(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = make_root(tmp_path, extra=ASSET)
    log = _log(tmp_path / "logs")
    _stubbed_env(monkeypatch)
    out = tmp_path / "out"
    code = main(
        [
            "run",
            "--root",
            str(root),
            "--logs",
            str(log),
            "--out",
            str(out),
            "inspect_evals/stereoset",
        ]
    )
    assert code == 1  # the header producer skipped: its only log is a mock run
    summary = (out / "inspect-evals-stereoset" / "SUMMARY.md").read_text()
    assert "all excluded" in summary and "mockllm" in summary


def test_malformed_config_is_a_usage_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = make_root(tmp_path, extra=ASSET)
    bad = tmp_path / "bad.yaml"
    bad.write_text("evals:\n  inspect_evals/stereoset:\n    dataset:\n      pth: x\n")
    code = main(
        [
            "run",
            "--root",
            str(root),
            "--out",
            str(tmp_path / "out"),
            "--config",
            str(bad),
            "inspect_evals/stereoset",
        ]
    )
    assert code == 2
    assert "bad.yaml" in capsys.readouterr().err


def test_review_files_beside_the_out_dir_are_applied(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    root = make_root(tmp_path, extra=ASSET)
    log = _log(tmp_path / "logs")
    _stubbed_env(monkeypatch)
    out = tmp_path / "out"
    out.mkdir()
    (out / "suppressions.yaml").write_text(
        "- rule: duplicate_questions\n  subject: inspect_evals/stereoset\n  author: matt\n"
        "  reason: known duplicates\n  since: 2026-09-30\n"
    )
    args = [
        "run",
        "--config",
        str(PILOT),
        "--root",
        str(root),
        "--logs",
        str(log),
        "--out",
        str(out),
    ]
    assert main([*args, "inspect_evals/stereoset"]) == 0
    findings = pd.read_parquet(out / "findings.parquet")
    dup = findings[findings["rule"] == "duplicate_questions"]
    assert len(dup) == 18 and bool(dup["suppressed"].all())  # kept in the parquet, marked
    summary = (out / "inspect-evals-stereoset" / "SUMMARY.md").read_text()
    assert "## Suppressed" in summary
    assert (
        "duplicate_questions · 18 observations · false_positive · matt: known duplicates" in summary
    )
    # the run files on disk are untouched
    current = json.loads((out / "inspect-evals-stereoset" / "current.json").read_text())
    stored = json.loads((out / "inspect-evals-stereoset" / current["inspect_dataset"]).read_text())
    assert all(f["suppressions"] == [] for f in stored["findings"])
    # an issue linking a fingerprint the sweep produced, plus one it did not
    fingerprint = str(dup.iloc[0]["fingerprint"])
    (out / "issues.yaml").write_text(
        f"- id: ISS-0001\n  title: dups\n  subject: inspect_evals/stereoset\n"
        f"  findings: [{fingerprint}, sha256:gone]\n  author: matt\n  opened: 2026-09-30\n"
    )
    assert main(["summary", str(out)]) == 0
    findings = pd.read_parquet(out / "findings.parquet")
    assert int((findings["issue"] == "ISS-0001").sum()) == 1
    assert (
        "- ISS-0001 · dups · 1 current observation"
        in (out / "inspect-evals-stereoset" / "SUMMARY.md").read_text()
    )
    assert "sha256:gone" in capsys.readouterr().err


def test_malformed_review_file_is_a_usage_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    root = make_root(tmp_path, extra=ASSET)
    _stubbed_env(monkeypatch)
    out = tmp_path / "out"
    out.mkdir()
    (out / "issues.yaml").write_text("- id: ISS-1\n  bogus: 1\n")
    code = main(
        [
            "run",
            "--config",
            str(PILOT),
            "--root",
            str(root),
            "--out",
            str(out),
            "inspect_evals/stereoset",
        ]
    )
    assert code == 2
    assert "issues.yaml" in capsys.readouterr().err


def test_a_renamed_run_file_carries_its_own_record_ids(tmp_path: Path, run: Run) -> None:
    from inspect_audit.findings.cli import write_outputs
    from inspect_audit.findings.io import read_run

    out = tmp_path / "out"
    write_outputs(out, {"inspect_evals/stereoset": [run]})
    write_outputs(out, {"inspect_evals/stereoset": [run]})  # same run id within one second
    runs_dir = out / "inspect-evals-stereoset" / "runs"
    first = read_run(runs_dir / "lint-1.run.json")
    second = read_run(runs_dir / "lint-1-2.run.json")
    assert [f.id for f in first.findings] == ["lint-1/1"]
    assert second.id == "lint-1-2"
    assert [f.id for f in second.findings] == ["lint-1-2/1"]
    assert [f.run_id for f in second.findings] == ["lint-1-2"]


def test_issue_with_a_subject_no_eval_matches_is_warned_about(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    root = make_root(tmp_path, extra=ASSET)
    _stubbed_env(monkeypatch)
    out = tmp_path / "out"
    assert (
        main(
            [
                "run",
                "--config",
                str(PILOT),
                "--root",
                str(root),
                "--out",
                str(out),
                "inspect_evals/stereoset",
            ]
        )
        == 0
    )
    findings = pd.read_parquet(out / "findings.parquet")
    fingerprint = str(findings.iloc[0]["fingerprint"])
    (out / "issues.yaml").write_text(
        f"- id: ISS-0002\n  title: typo\n  subject: inspect_evals/steroset\n  findings: [{fingerprint}]\n"
        "  author: matt\n  opened: 2026-09-30\n"
    )
    assert main(["summary", str(out)]) == 0
    err = capsys.readouterr().err
    assert "ISS-0002" in err and "inspect_evals/steroset" in err


def test_explicit_review_dir_that_does_not_exist_is_a_usage_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    root = make_root(tmp_path, extra=ASSET)
    _stubbed_env(monkeypatch)
    out = tmp_path / "out"
    code = main(
        [
            "run",
            "--config",
            str(PILOT),
            "--root",
            str(root),
            "--out",
            str(out),
            "--review",
            str(tmp_path / "typo"),
            "inspect_evals/stereoset",
        ]
    )
    assert code == 2 and "typo" in capsys.readouterr().err
    assert not (out / "inspect-evals-stereoset").exists()  # nothing ran


def test_leads_subcommand_prints_and_writes(
    tmp_path: Path, run: Run, capsys: pytest.CaptureFixture[str]
) -> None:
    from inspect_audit.findings.cli import write_outputs

    out = tmp_path / "out"
    write_outputs(out, {"inspect_evals/stereoset": [run]})
    assert main(["leads", "--out", str(out), "inspect_evals/stereoset"]) == 0
    printed = capsys.readouterr().out
    assert printed.startswith("# Leads for inspect_evals/stereoset") and "`lint-1/1`" in printed
    target = tmp_path / "LEADS.md"
    assert (
        main(["leads", "--out", str(out), "--write", str(target), "inspect_evals/stereoset"]) == 0
    )
    assert target.read_text() == printed


def test_leads_subcommand_with_no_runs_is_a_usage_error(
    tmp_path: Path, run: Run, capsys: pytest.CaptureFixture[str]
) -> None:
    from inspect_audit.findings.cli import write_outputs

    out = tmp_path / "out"
    write_outputs(out, {"inspect_evals/stereoset": [run]})
    assert main(["leads", "--out", str(out), "inspect_evals/hle"]) == 2
    err = capsys.readouterr().err
    assert "inspect_evals/hle" in err and str(out) in err
