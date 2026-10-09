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
from inspect_audit.findings.fs import StoreFS
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
    assert not (slug_dir / "current.json").exists()
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
    first = pd.read_parquet(out / "runs.parquet").set_index("producer")["run_id"]
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
    assert len(list((slug_dir / "runs").glob("*.run.json"))) == 4
    runs = pd.read_parquet(out / "runs.parquet")
    assert len(runs) == 3  # the current view, not every run ever written
    second = runs.set_index("producer")["run_id"]
    assert second["inspect_dataset"] == first["inspect_dataset"]
    assert second["inspect_audit_header"] == first["inspect_audit_header"]
    assert second["inspect_evals_lint"] != first["inspect_evals_lint"]
    lint_ids = sorted(
        p.name.removesuffix(".run.json")
        for p in (slug_dir / "runs").glob("inspect_evals_lint-*.run.json")
    )
    assert second["inspect_evals_lint"] == lint_ids[-1]  # the "-2" collision suffix sorts last
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


def test_review_decisions_in_the_store_are_applied(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
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
    author = ["--author", "Matt Fisher <matt@example.com>"]
    common = ["--store", str(out), *author]
    suppress = ["review", "suppress", *common, "--rule", "duplicate_questions"]
    assert (
        main([*suppress, "--eval", "inspect_evals/stereoset", "--reason", "known duplicates"]) == 0
    )
    findings = pd.read_parquet(out / "findings.parquet")
    dup = findings[findings["rule"] == "duplicate_questions"]
    assert len(dup) == 18 and bool(dup["suppressed"].all())  # kept in the parquet, marked
    summary = (out / "inspect-evals-stereoset" / "SUMMARY.md").read_text()
    assert "## Suppressed" in summary
    assert "duplicate_questions · 18 observations · false_positive · Matt Fisher" in summary
    # the run files on disk are untouched
    (stored_path,) = (out / "inspect-evals-stereoset" / "runs").glob("inspect_dataset-*.run.json")
    stored = json.loads(stored_path.read_text())
    assert all(f["suppressions"] == [] for f in stored["findings"])
    # a second sweep keeps the decision; the derived views carry the header
    assert main([*args, "inspect_evals/stereoset"]) == 0
    assert "## Suppressed" in (out / "inspect-evals-stereoset" / "SUMMARY.md").read_text()
    assert (out / "suppressions.yaml").read_text().startswith("# derived from review/")
    accept = ["review", "accept", *common, "--eval", "inspect_evals/stereoset"]
    assert main([*accept, "--rule", "duplicate_questions", "--title", "dups"]) == 0
    assert main(["summary", str(out)]) == 0
    findings = pd.read_parquet(out / "findings.parquet")
    assert int((findings["issue"] == "ISS-0001").sum()) == 18
    assert (
        "- ISS-0001 · dups · 18 current observations"
        in (out / "inspect-evals-stereoset" / "SUMMARY.md").read_text()
    )


def test_malformed_decision_object_is_a_usage_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    root = make_root(tmp_path, extra=ASSET)
    _stubbed_env(monkeypatch)
    out = tmp_path / "out"
    (out / "review").mkdir(parents=True)
    (out / "review" / "20261009T040000Z-dec-bad.json").write_text("{not json")
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
    assert "dec-bad" in capsys.readouterr().err
    assert not (out / "inspect-evals-stereoset").exists()  # nothing ran


def test_a_renamed_run_file_carries_its_own_record_ids(tmp_path: Path, run: Run) -> None:
    from inspect_audit.findings.cli import write_outputs
    from inspect_audit.findings.io import read_run

    out = tmp_path / "out"
    write_outputs(out, {"inspect_evals/stereoset": [run]})
    write_outputs(out, {"inspect_evals/stereoset": [run]})  # same run id within one second
    runs_dir = out / "inspect-evals-stereoset" / "runs"
    fs = StoreFS.from_locator(out)
    first = read_run(fs, "inspect-evals-stereoset/runs/lint-1.run.json")
    second = read_run(fs, "inspect-evals-stereoset/runs/lint-1-2.run.json")
    assert [f.id for f in first.findings] == ["lint-1/1"]
    assert second.id == "lint-1-2"
    assert [f.id for f in second.findings] == ["lint-1-2/1"]
    assert [f.run_id for f in second.findings] == ["lint-1-2"]


def test_issue_with_a_subject_no_eval_matches_is_warned_about(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from datetime import UTC, datetime

    from inspect_audit.findings.review import AcceptPayload, Decision, write_decision

    root = make_root(tmp_path, extra=ASSET)
    _stubbed_env(monkeypatch)
    out = tmp_path / "out"
    args = ["run", "--config", str(PILOT), "--root", str(root), "--out", str(out)]
    assert main([*args, "inspect_evals/stereoset"]) == 0
    findings = pd.read_parquet(out / "findings.parquet")
    fingerprint = str(findings.iloc[0]["fingerprint"])
    at = datetime(2026, 9, 30, tzinfo=UTC)
    typo = Decision(
        id="dec-typo",
        at=at,
        author="matt <m@x>",
        kind="accept",
        accept=AcceptPayload(
            issue="ISS-0002",
            title="typo",
            subject="inspect_evals/steroset",
            fingerprints=[fingerprint],
        ),
    )
    write_decision(StoreFS.from_locator(out), typo)
    assert main(["summary", str(out)]) == 0
    err = capsys.readouterr().err
    assert "ISS-0002" in err and "inspect_evals/steroset" in err


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


def test_review_cli_suppress_accept_link(
    tmp_path: Path, run: Run, capsys: pytest.CaptureFixture[str]
) -> None:
    from inspect_audit.findings.cli import write_outputs
    from inspect_audit.findings.review import load_review

    out = tmp_path / "out"
    write_outputs(out, {"inspect_evals/stereoset": [run]})
    common = ["--out", str(out), "--author", "Matt Fisher <matt@example.com>"]
    eval_rule = ["--eval", "inspect_evals/stereoset", "--rule", "IEBP008"]
    assert main(["review", "suppress", *common, *eval_rule, "--reason", "struct answers"]) == 0
    assert load_review(out).suppressions[0].reason == "struct answers"
    assert main(["review", "accept", *common, *eval_rule, "--title", "dup filter"]) == 0
    issue = load_review(out).issues[0]
    assert issue.id == "ISS-0001" and issue.findings == ["sha256:0"]
    assert "ISS-0001" in capsys.readouterr().out
    url = "https://github.com/x/y/issues/1"
    assert main(["review", "link", *common, "ISS-0001", url]) == 0
    assert load_review(out).issues[0].github == url
    # a conflict is a usage error carrying the Store's message, not a traceback
    assert main(["review", "accept", *common, "--id", "lint-1/1", "--title", "again"]) == 2
    assert "ISS-0001" in capsys.readouterr().err


def test_review_cli_author_falls_back_to_env_then_git_config_then_fails(
    tmp_path: Path, run: Run, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import subprocess

    from inspect_audit.findings.cli import write_outputs
    from inspect_audit.findings.review import load_review

    out = tmp_path / "out"
    write_outputs(out, {"inspect_evals/stereoset": [run]})
    monkeypatch.delenv("INSPECT_AUDIT_AUTHOR", raising=False)
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(tmp_path / "none"))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    (tmp_path / "elsewhere").mkdir()
    monkeypatch.chdir(tmp_path / "elsewhere")
    args = ["review", "suppress", "--store", str(out), "--rule", "IEBP008", "--reason", "r"]
    assert main(args) == 2
    assert "author" in capsys.readouterr().err
    git = ["git", "-C", str(tmp_path / "elsewhere")]
    subprocess.run([*git, "init", "-q"], check=True)
    subprocess.run([*git, "config", "user.name", "Ada"], check=True)
    subprocess.run([*git, "config", "user.email", "ada@example.com"], check=True)
    assert main(args) == 0
    assert load_review(StoreFS.from_locator(out)).suppressions[0].author == "Ada <ada@example.com>"
    monkeypatch.setenv("INSPECT_AUDIT_AUTHOR", "Env Person <env@example.com>")
    status = [
        "review",
        "status",
        "--store",
        str(out),
        "--id",
        "lint-1/1",
        "qualified",
        "--reason",
        "r",
    ]
    assert main(status) == 0
    assert load_review(StoreFS.from_locator(out)).status_provenance["sha256:0"].author == (
        "Env Person <env@example.com>"
    )


def test_review_cli_malformed_decision_is_a_usage_error(
    tmp_path: Path, run: Run, capsys: pytest.CaptureFixture[str]
) -> None:
    from inspect_audit.findings.cli import write_outputs

    out = tmp_path / "out"
    write_outputs(out, {"inspect_evals/stereoset": [run]})
    (out / "review").mkdir(exist_ok=True)
    (out / "review" / "20261009T040000Z-dec-bad.json").write_text("- [unclosed\n")
    args = ["review", "suppress", "--store", str(out), "--rule", "IEBP008", "--reason", "r"]
    assert main([*args, "--author", "Matt Fisher <matt@example.com>"]) == 2
    assert "dec-bad" in capsys.readouterr().err


def test_store_flag_and_environment_default(
    tmp_path: Path, run: Run, monkeypatch: pytest.MonkeyPatch
) -> None:
    from inspect_audit.findings.cli import write_outputs
    from inspect_audit.findings.review import load_review

    out = tmp_path / "out"
    write_outputs(out, {"inspect_evals/stereoset": [run]})
    author = ["--author", "Matt Fisher <matt@example.com>"]
    suppress = ["review", "suppress", "--store", str(out), "--rule", "IEBP008", "--reason", "r"]
    assert main([*suppress, *author]) == 0
    monkeypatch.setenv("INSPECT_AUDIT_STORE", str(out))
    status = ["review", "status", "--fingerprint", "sha256:0", "retracted", "--reason", "fixed"]
    assert main([*status, *author]) == 0
    review = load_review(StoreFS.from_locator(out))
    assert review.suppressions and review.statuses == {"sha256:0": "retracted"}
    assert main(["summary"]) == 0


def test_review_retract_and_migrate(
    tmp_path: Path, run: Run, capsys: pytest.CaptureFixture[str]
) -> None:
    import yaml

    from inspect_audit.findings.cli import write_outputs

    out = tmp_path / "out"
    write_outputs(out, {"inspect_evals/stereoset": [run]})
    legacy = [
        {
            "rule": "IEBP008",
            "subject": "*",
            "author": "old <o@x>",
            "reason": "legacy",
            "since": "2026-09-30",
        }
    ]
    (out / "suppressions.yaml").write_text(yaml.safe_dump(legacy))
    assert main(["review", "migrate", "--store", str(out)]) == 0
    assert "1 decision" in capsys.readouterr().out
    (key,) = StoreFS.from_locator(out).glob("review/*.json")
    decision_id = "dec-" + key.rsplit("-dec-", 1)[1].removesuffix(".json")
    author = ["--author", "Matt Fisher <matt@example.com>"]
    retract = ["review", "retract", "--store", str(out), decision_id, "--reason", "not noise"]
    assert main([*retract, *author]) == 0
    assert (out / "suppressions.yaml").read_text().strip().endswith("[]")


def test_run_requires_a_local_store_for_now(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = main(["run", "--root", str(tmp_path), "--store", "memory://remote", "inspect_evals/x"])
    assert code == 2
    assert "local" in capsys.readouterr().err


def test_render_current_writes_the_export(tmp_path: Path, run: Run) -> None:
    import json

    from inspect_audit.findings.cli import write_outputs

    out = tmp_path / "out"
    write_outputs(out, {"inspect_evals/stereoset": [run]})
    index = json.loads((out / "export" / "index.json").read_text())
    assert index["schema"] == 1
    assert [e["eval"] for e in index["evals"]] == ["inspect_evals/stereoset"]
    assert (out / "export" / "evals" / "inspect-evals-stereoset.json").is_file()
    assert "secret" not in (out / "export" / "index.json").read_text()
