"""End to end over a temporary inspect_evals root with stubbed producers."""

import io
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
    (tmp_path / "src" / "inspect_evals").mkdir(parents=True)  # a checkout, as far as run checks
    from inspect_audit.findings import cli

    seen: list[list[str]] = []
    monkeypatch.setattr(
        cli, "sweep", lambda targets, ctx, producers, **kw: seen.append(list(targets)) or {}
    )
    monkeypatch.setattr(cli, "write_outputs", lambda out, runs, review=None, **_: None)
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
    (tmp_path / "src" / "inspect_evals").mkdir(parents=True)  # a checkout, as far as run checks
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
    monkeypatch.setattr(cli, "write_outputs", lambda out, runs, review=None, **_: None)
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


def _remote_sweep(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, locator: str) -> int:
    root = make_root(tmp_path, extra=ASSET)
    _stubbed_env(monkeypatch)
    args = ["run", "--config", str(PILOT), "--root", str(root), "--store", locator]
    return main([*args, "--producers", "dataset", "inspect_evals/stereoset"])


def test_run_writes_runs_and_scan_directories_to_a_remote_store(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import tempfile

    from inspect_audit.findings.io import read_run

    scratch = Path(tempfile.gettempdir())
    before = set(scratch.glob("inspect_audit_run_*"))
    assert _remote_sweep(tmp_path, monkeypatch, "memory://remote-run") == 0
    fs = StoreFS.from_locator("memory://remote-run")
    (run_key,) = fs.glob("inspect-evals-stereoset/runs/inspect_dataset-*.run.json")
    stored = read_run(fs, run_key)
    assert stored.inputs["scan_dir"] == f"inspect-evals-stereoset/scans/{stored.id}"
    assert fs.is_file(f"{stored.inputs['scan_dir']}/scan_summary.json")
    assert fs.is_file("export/index.json") and fs.is_file("inspect-evals-stereoset/SUMMARY.md")
    assert set(scratch.glob("inspect_audit_run_*")) == before  # the scratch dir is gone
    assert main(["summary", "--store", "memory://remote-run"]) == 0


def test_remote_rerun_in_the_same_second_keeps_both_scan_directories(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from inspect_audit.findings.adapters import dataset as dataset_adapter
    from inspect_audit.findings.io import read_run

    frozen = dataset_adapter.utcnow()
    monkeypatch.setattr(dataset_adapter, "utcnow", lambda: frozen)
    assert _remote_sweep(tmp_path / "a", monkeypatch, "memory://remote-twice") == 0
    assert _remote_sweep(tmp_path / "b", monkeypatch, "memory://remote-twice") == 0
    fs = StoreFS.from_locator("memory://remote-twice")
    keys = fs.glob("inspect-evals-stereoset/runs/inspect_dataset-*.run.json")
    assert len(keys) == 2 and any(k.endswith("-2.run.json") for k in keys)
    for key in keys:
        stored = read_run(fs, key)
        assert stored.inputs["scan_dir"] == f"inspect-evals-stereoset/scans/{stored.id}"
        assert fs.is_file(f"{stored.inputs['scan_dir']}/scan_summary.json")


def test_a_skipped_remote_dataset_run_uploads_no_scan_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = make_root(tmp_path, extra=ASSET)
    _stubbed_env(monkeypatch)
    monkeypatch.setenv("STUB_EXIT", "1")  # the scan itself fails, so the dataset run is a skip
    args = ["run", "--config", str(PILOT), "--root", str(root), "--store", "memory://remote-skip"]
    assert main([*args, "--producers", "dataset", "inspect_evals/stereoset"]) == 1
    fs = StoreFS.from_locator("memory://remote-skip")
    assert fs.glob("inspect-evals-stereoset/scans/*") == []
    assert fs.glob("inspect-evals-stereoset/runs/*.run.json")  # the skip itself is recorded


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


def _without_clock_lines(text: str) -> str:
    """Lines carrying wall-clock time or a time-derived id, which differ between two renders."""
    return "\n".join(
        line
        for line in text.splitlines()
        if "generated_at" not in line and "opened" not in line and "dec-" not in line
    )


def test_render_is_identical_on_a_directory_and_in_memory(tmp_path: Path, run: Run) -> None:
    from inspect_audit.findings.cli import write_outputs
    from inspect_audit.findings.store import Selection, Store

    stores = [StoreFS.from_locator(tmp_path / "a"), StoreFS.from_locator("memory://parity")]
    keys = [
        "SUMMARY.md",
        "inspect-evals-stereoset/SUMMARY.md",
        "export/index.json",
        "export/evals/inspect-evals-stereoset.json",
        "issues.yaml",
    ]
    outputs: list[dict[str, str]] = []
    frames: list[list[pd.DataFrame]] = []
    for fs in stores:
        write_outputs(fs, {"inspect_evals/stereoset": [run]})
        Store(fs).accept(Selection(ids=("lint-1/1",)), title="t", author="Matt Fisher <m@x>")
        outputs.append({k: _without_clock_lines(fs.read_text(k)) for k in keys})
        frames.append(
            [
                pd.read_parquet(io.BytesIO(fs.read_bytes(k)))
                for k in ("findings.parquet", "runs.parquet")
            ]
        )
    assert outputs[0] == outputs[1]
    assert all(a.equals(b) for a, b in zip(frames[0], frames[1], strict=True))


def test_hand_written_review_files_must_be_migrated_first(
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
    assert main(["summary", str(out)]) == 2
    assert "migrate" in capsys.readouterr().err
    assert (out / "suppressions.yaml").read_text().startswith("- ")  # untouched
    suppress = ["review", "suppress", "--store", str(out), "--rule", "IEBP008", "--reason", "r"]
    assert main([*suppress, "--author", "Matt Fisher <matt@example.com>"]) == 2
    assert "migrate" in capsys.readouterr().err
    assert StoreFS.from_locator(out).glob("review/*.json") == []
    assert main(["review", "migrate", "--store", str(out)]) == 0
    assert main(["summary", str(out)]) == 0
    assert (out / "suppressions.yaml").read_text().startswith("# derived from review/")
    assert "IEBP008" in (out / "suppressions.yaml").read_text()


def test_summary_positional_beats_the_environment_store(
    tmp_path: Path, run: Run, monkeypatch: pytest.MonkeyPatch
) -> None:
    from inspect_audit.findings.cli import write_outputs

    out = tmp_path / "out"
    write_outputs(out, {"inspect_evals/stereoset": [run]})
    monkeypatch.setenv("INSPECT_AUDIT_STORE", str(tmp_path / "nonexistent"))
    assert main(["summary", str(out)]) == 0
    assert main(["summary", "--store", str(out)]) == 0
    assert main(["summary"]) == 2


def test_write_outputs_names_a_staged_scan_directory_after_its_run(
    tmp_path: Path, run: Run
) -> None:
    from inspect_audit.findings.cli import write_outputs
    from inspect_audit.findings.io import read_run

    out = tmp_path / "out"
    fs = StoreFS.from_locator(out)
    keys = []
    for name in ("one", "two"):  # two runs with the same id: the second file gets -2
        staged = f"inspect-evals-stereoset/scans/.lint-1.{name}"
        (out / staged).mkdir(parents=True)
        (out / staged / f"{name}.json").write_text("{}")
        with_scan = run.model_copy(update={"inputs": {"scan_dir": staged}})
        write_outputs(out, {"inspect_evals/stereoset": [with_scan]}, work_dir=out)
        assert not (out / staged).exists()
    for file_id, name in (("lint-1", "one"), ("lint-1-2", "two")):
        stored = read_run(fs, f"inspect-evals-stereoset/runs/{file_id}.run.json")
        assert stored.inputs["scan_dir"] == f"inspect-evals-stereoset/scans/{file_id}"
        assert (out / str(stored.inputs["scan_dir"]) / f"{name}.json").is_file()
        keys.append(stored.inputs["scan_dir"])
    assert keys[0] != keys[1]


def test_a_leftover_scan_directory_with_no_run_is_replaced(tmp_path: Path, run: Run) -> None:
    from inspect_audit.findings.cli import write_outputs

    out = tmp_path / "out"
    (out / "inspect-evals-stereoset/scans/lint-1").mkdir(parents=True)
    (out / "inspect-evals-stereoset/scans/lint-1/stale.json").write_text("{}")  # a crash's orphan
    staged = "inspect-evals-stereoset/scans/.lint-1.new"
    (out / staged).mkdir(parents=True)
    (out / staged / "fresh.json").write_text("{}")
    with_scan = run.model_copy(update={"inputs": {"scan_dir": staged}})
    write_outputs(out, {"inspect_evals/stereoset": [with_scan]}, work_dir=out)
    final = out / "inspect-evals-stereoset/scans/lint-1"
    assert (final / "fresh.json").is_file() and not (final / "stale.json").exists()


def test_local_same_second_sweeps_keep_both_scan_directories(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from inspect_audit.findings.adapters import dataset as dataset_adapter
    from inspect_audit.findings.io import read_run

    frozen = dataset_adapter.utcnow()
    monkeypatch.setattr(dataset_adapter, "utcnow", lambda: frozen)
    store = tmp_path / "store"
    assert _remote_sweep(tmp_path / "a", monkeypatch, str(store)) == 0
    monkeypatch.setenv("STUB_EXIT", "1")  # the second scan fails after writing partial output
    assert _remote_sweep(tmp_path / "b", monkeypatch, str(store)) == 1
    monkeypatch.delenv("STUB_EXIT")
    assert _remote_sweep(tmp_path / "c", monkeypatch, str(store)) == 0
    fs = StoreFS.from_locator(store)
    cited = [
        read_run(fs, k).inputs.get("scan_dir")
        for k in fs.glob("inspect-evals-stereoset/runs/inspect_dataset-*.run.json")
    ]
    present = [c for c in cited if c is not None]
    assert len(present) == 2 and len(set(present)) == 2
    for key in present:
        assert (store / str(key) / "scan_summary.json").is_file()


def test_a_store_write_failure_is_a_usage_error_not_a_skip(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from inspect_audit.findings import cli

    def failing(*_args: object, **_kwargs: object) -> None:
        raise OSError("connection reset by bucket")

    monkeypatch.setattr(cli, "write_outputs", failing)
    assert _remote_sweep(tmp_path, monkeypatch, "memory://remote-fails") == 2
    assert "connection reset by bucket" in capsys.readouterr().err


def test_render_reads_and_writes_the_store_in_batches(
    tmp_path: Path, run: Run, monkeypatch: pytest.MonkeyPatch
) -> None:
    """On object storage every per-object call is a round trip; rendering must batch them."""
    from inspect_audit.findings.cli import render_current, write_outputs
    from inspect_audit.findings.store import Store

    fs = StoreFS.from_locator("memory://batched-render")
    evals = [f"inspect_evals/e{i}" for i in range(6)]
    write_outputs(
        fs,
        {
            e: [run.model_copy(update={"subject": run.subject.model_copy(update={"eval": e})})]
            for e in evals
        },
    )
    Store(fs).suppress(rule="IEBP008", author="Matt Fisher <m@x>", reason="r")
    calls: dict[str, int] = {}
    for name in ("read_text", "read_bytes", "write_text", "is_file", "exists"):
        original = getattr(StoreFS, name)

        def counted(
            self: StoreFS, *args: object, _name: str = name, _f: object = original, **kw: object
        ) -> object:
            calls[_name] = calls.get(_name, 0) + 1
            return _f(self, *args, **kw)  # type: ignore[operator]

        monkeypatch.setattr(StoreFS, name, counted)
    render_current(fs)
    assert calls.get("read_text", 0) == 0 and calls.get("write_text", 0) == 0
    assert calls.get("read_bytes", 0) == 0
    assert calls.get("is_file", 0) + calls.get("exists", 0) == 0
    assert fs.read_text("inspect-evals-e5/SUMMARY.md").startswith("# inspect_evals/e5")


def test_run_takes_its_root_from_audit_toml(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = make_root(tmp_path, extra=ASSET)
    _stubbed_env(monkeypatch)
    store = tmp_path / "store"
    store.mkdir()
    monkeypatch.chdir(tmp_path)  # a relative root resolves against the current directory
    (store / "audit.toml").write_text(f'[evals]\nroot = "{root.relative_to(tmp_path)}"\n')
    args = ["run", "--config", str(PILOT), "--store", str(store), "--producers", "lint"]
    assert main([*args, "inspect_evals/stereoset"]) == 0
    assert list((store / "inspect-evals-stereoset" / "runs").glob("inspect_evals_lint-*.run.json"))


def test_root_on_the_command_line_beats_audit_toml(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = make_root(tmp_path, extra=ASSET)
    _stubbed_env(monkeypatch)
    store = tmp_path / "store"
    store.mkdir()
    (store / "audit.toml").write_text('[evals]\nroot = "/nowhere/at/all"\n')
    args = ["run", "--config", str(PILOT), "--root", str(root), "--store", str(store)]
    assert main([*args, "--producers", "lint", "inspect_evals/stereoset"]) == 0


def test_run_without_a_root_anywhere_is_a_usage_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["run", "--store", str(tmp_path / "store"), "inspect_evals/x"]) == 2
    err = capsys.readouterr().err
    assert "--root" in err and "audit.toml" in err


def test_run_uses_the_stores_pilot_when_no_config_is_given(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from inspect_audit.findings.io import read_run

    root = make_root(tmp_path, extra=ASSET)
    _stubbed_env(monkeypatch)
    store = tmp_path / "store"
    store.mkdir()
    (store / "pilot.yaml").write_text(
        PILOT.read_text()
    )  # declares StereoSet's intersentence config
    args = ["run", "--root", str(root), "--store", str(store), "--producers", "dataset"]
    assert main([*args, "inspect_evals/stereoset"]) == 0
    fs = StoreFS.from_locator(store)
    (key,) = fs.glob("inspect-evals-stereoset/runs/inspect_dataset-*.run.json")
    dataset = read_run(fs, key).inputs["dataset"]
    assert isinstance(dataset, dict) and dataset.get("config") == "intersentence"


def test_a_malformed_audit_toml_is_a_usage_error_before_anything_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    root = make_root(tmp_path, extra=ASSET)
    _stubbed_env(monkeypatch)
    store = tmp_path / "store"
    store.mkdir()
    (store / "audit.toml").write_text("[evals\n")
    args = ["run", "--root", str(root), "--store", str(store), "inspect_evals/stereoset"]
    assert main(args) == 2
    assert "audit.toml" in capsys.readouterr().err
    assert not (store / "inspect-evals-stereoset").exists()


def _remote_log_dir(tmp_path: Path, url: str) -> Path:
    """A real .eval written locally, copied into an fsspec location as a remote log source."""
    from fsspec.core import url_to_fs

    local = _log(tmp_path / "made")
    fs, root = url_to_fs(url)
    fs.makedirs(f"{root}/nested", exist_ok=True)
    fs.put(str(local), f"{root}/nested/{local.name}")
    return local


def test_logs_from_an_fsspec_location_are_fetched_once_into_the_cache(tmp_path: Path) -> None:
    from fsspec.core import url_to_fs

    local = _remote_log_dir(tmp_path, "memory://remote-logs-a")
    cache = tmp_path / "cache"
    paths = collect_logs(["memory://remote-logs-a"], remote_cache=cache)
    assert [p.name for p in paths] == [local.name]
    assert paths[0].is_file() and paths[0].is_relative_to(cache)
    assert paths[0].read_bytes() == local.read_bytes()
    fs, _ = url_to_fs("memory://remote-logs-a")
    fetched: list[str] = []
    inner_get = fs.get_file

    def counting_get(rpath: str, lpath: str, **kwargs: object) -> None:
        fetched.append(rpath)
        inner_get(rpath, lpath, **kwargs)

    fs.get_file = counting_get  # type: ignore[method-assign]
    try:
        again = collect_logs(["memory://remote-logs-a"], remote_cache=cache)
    finally:
        fs.get_file = inner_get  # type: ignore[method-assign]
    assert again == paths and fetched == []


def test_a_remote_log_source_with_no_logs_names_itself(tmp_path: Path) -> None:
    from fsspec.core import url_to_fs

    from inspect_audit.findings.adapters import ProducerError

    fs, root = url_to_fs("memory://remote-logs-empty")
    fs.pipe(f"{root}/notes.txt", b"not a log")
    with pytest.raises(ProducerError, match="memory://remote-logs-empty"):
        collect_logs(["memory://remote-logs-empty"], remote_cache=tmp_path / "cache")


def test_run_reads_header_logs_from_a_remote_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = make_root(tmp_path, extra=ASSET)
    _stubbed_env(monkeypatch)
    _remote_log_dir(tmp_path, "memory://remote-logs-run")
    cache, store = tmp_path / "log-cache", tmp_path / "store"
    args = ["run", "--config", str(PILOT), "--root", str(root), "--store", str(store)]
    args += ["--logs", "memory://remote-logs-run", "--log-cache", str(cache), "--producers", "lint"]
    assert main([*args, "inspect_evals/stereoset"]) == 0
    assert list(cache.rglob("*.eval"))
    assert list(
        (store / "inspect-evals-stereoset" / "runs").glob("inspect_audit_header-*.run.json")
    )


def test_hawk_pull_needs_an_explicit_manifest() -> None:
    with pytest.raises(SystemExit) as exit_info:
        main(["hawk-pull"])
    assert exit_info.value.code == 2


def test_the_findings_package_names_no_organisation() -> None:
    import re

    package = Path(__file__).parents[2]
    pattern = re.compile(
        r"generality|4dd4a6b0|inspect-evals-findings|audits\.generality", re.IGNORECASE
    )
    hits = [
        f"{path.relative_to(package)}:{n}"
        for folder in ("src/inspect_audit/findings", "scripts")
        for path in sorted((package / folder).rglob("*"))
        if path.is_file()
        and path.suffix in {".py", ".yaml", ".yml", ".toml", ".json", ".md", ".sh"}
        and "taxonomies" not in path.parts  # credit where a framework came from: provenance
        for n, line in enumerate(path.read_text(errors="ignore").splitlines(), 1)
        if pattern.search(line)
    ]
    assert hits == []


def test_a_single_remote_log_file_keeps_its_name(tmp_path: Path) -> None:
    from fsspec.core import url_to_fs

    local = _log(tmp_path / "made")
    fs, root = url_to_fs("memory://single-log")
    fs.put(str(local), f"{root}/{local.name}")
    (path,) = collect_logs([f"memory://single-log/{local.name}"], remote_cache=tmp_path / "cache")
    assert path.name == local.name and path.read_bytes() == local.read_bytes()


@pytest.mark.parametrize("url", ["nosuch://bucket/logs", "memory://listing-fails"])
def test_a_remote_log_source_that_cannot_be_listed_is_a_producer_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, url: str
) -> None:
    from fsspec.implementations.memory import MemoryFileSystem

    from inspect_audit.findings.adapters import ProducerError

    def denied(*_args: object, **_kwargs: object) -> object:
        raise PermissionError("access denied")

    monkeypatch.setattr(MemoryFileSystem, "find", denied)
    with pytest.raises(ProducerError, match=url.split("://")[0]):
        collect_logs([url], remote_cache=tmp_path / "cache")


def test_a_prefix_with_only_non_log_json_has_no_logs(tmp_path: Path) -> None:
    from fsspec.core import url_to_fs

    from inspect_audit.findings.adapters import ProducerError

    fs, root = url_to_fs("memory://only-eval-set-json")
    fs.pipe(f"{root}/eval-set.json", b"{}")
    with pytest.raises(ProducerError, match=r"no \.eval or \.json logs"):
        collect_logs(["memory://only-eval-set-json"], remote_cache=tmp_path / "cache")


def test_a_root_that_is_not_an_inspect_evals_checkout_stops_the_run(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    store = tmp_path / "store"
    store.mkdir()
    (store / "audit.toml").write_text('[evals]\nroot = "../nowhere"\n')
    assert main(["run", "--store", str(store), "--producers", "lint", "inspect_evals/x"]) == 2
    err = capsys.readouterr().err
    assert "nowhere" in err and "audit.toml" in err
    assert not any(store.glob("*/runs"))
