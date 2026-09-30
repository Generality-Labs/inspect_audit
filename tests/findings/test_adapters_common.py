"""Producer configuration, subject derivation and the never-raise command runner."""

import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

from inspect_audit.findings.adapters import (
    Context,
    ProducerError,
    eval_yaml,
    new_run_id,
    package_of,
    repo_revision,
    run_command,
    skip_run,
    slug,
    subject_for,
    task_version_from_yaml,
)
from inspect_audit.findings.config import Config, EvalConfig, LogFilter
from inspect_audit.findings.producers import (
    DATASET_SPEC,
    DATASET_TASK_SPEC,
    LINT_SPEC,
    ProducerConfig,
)

STUBS = Path(__file__).parent / "stubs"


# the test logs are produced by mockllm/model, which the default filter excludes on purpose
MOCK_OK = Config(defaults=EvalConfig(logs=LogFilter(include_mock=True)))


def make_root(tmp_path: Path, *, version: str = "3-A", extra: str = "") -> Path:
    root = tmp_path / "ie"
    package = root / "src" / "inspect_evals" / "stereoset"
    package.mkdir(parents=True)
    (package / "eval.yaml").write_text(
        f'title: StereoSet\nversion: "{version}"\ntasks:\n  - name: stereoset\n    dataset_samples: 2123\n{extra}'
    )
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    subprocess.run(
        ["git", "-C", str(root), "-c", "user.email=t@t", "-c", "user.name=t", "add", "."],
        check=True,
    )
    subprocess.run(
        [
            "git",
            "-C",
            str(root),
            "-c",
            "user.email=t@t",
            "-c",
            "user.name=t",
            "commit",
            "-q",
            "-m",
            "init",
        ],
        check=True,
    )
    return root


def make_ctx(root: Path, tmp_path: Path) -> Context:
    return Context(ie_root=root, logs=[], out_dir=tmp_path / "out", producers=ProducerConfig())


def test_default_prefixes_pin_the_specs() -> None:
    config = ProducerConfig()
    assert config.lint == ("uvx", "--from", LINT_SPEC, "inspect-evals-lint")
    assert config.dataset == ("uvx", "--from", DATASET_SPEC, "inspect-dataset")
    assert LINT_SPEC == "inspect-evals-lint==0.9.0"
    assert DATASET_SPEC == "git+https://github.com/Generality-Labs/inspect_dataset@afbc94c0b509"


def test_env_overrides_are_shell_split() -> None:
    config = ProducerConfig.from_env({"INSPECT_AUDIT_LINT_CMD": "python '/tmp/my stub.py' --flag"})
    assert config.lint == ("python", "/tmp/my stub.py", "--flag")
    assert config.dataset == ProducerConfig().dataset


def test_task_scans_dump_in_the_eval_project_and_scan_in_inspect_datasets_own_env() -> None:
    config = ProducerConfig()
    assert config.dataset_dump == (
        "uv",
        "run",
        "--project",
        "{ie_root}",
        "--frozen",
        "{eval_deps}",
        "python",
    )
    assert config.dataset_task == (
        "uvx",
        "--from",
        DATASET_TASK_SPEC,
        "{inspect_ai}",
        "inspect-dataset",
    )
    assert f"inspect-dataset[inspect] @ {DATASET_SPEC}" == DATASET_TASK_SPEC
    assert config.eval_env is None


def test_task_scan_commands_and_environment_can_be_overridden() -> None:
    config = ProducerConfig.from_env(
        {
            "INSPECT_AUDIT_DATASET_DUMP_CMD": "python dump.py {ie_root}",
            "INSPECT_AUDIT_DATASET_TASK_CMD": "python scan.py",
            "INSPECT_AUDIT_EVAL_ENV": "/tmp/env",
        }
    )
    assert config.dataset_dump == ("python", "dump.py", "{ie_root}")
    assert config.dataset_task == ("python", "scan.py")
    assert config.eval_env == "/tmp/env"


def test_package_of_and_slug() -> None:
    assert package_of("inspect_evals/stereoset") == "stereoset"
    assert package_of("stereoset") == "stereoset"
    assert slug("inspect_evals/SWE Lancer") == "inspect-evals-swe-lancer"


def test_eval_yaml_and_task_version(tmp_path: Path) -> None:
    root = make_root(tmp_path)
    data = eval_yaml(root, "inspect_evals/stereoset")
    assert data["tasks"][0]["dataset_samples"] == 2123
    parsed = task_version_from_yaml(data)
    assert parsed is not None and parsed.comparability == 3 and parsed.interface == "A"
    assert eval_yaml(root, "inspect_evals/missing") == {}
    assert task_version_from_yaml({}) is None


def test_repo_revision_reads_git(tmp_path: Path) -> None:
    root = make_root(tmp_path)
    revision = repo_revision(root)
    assert revision.commit is not None and len(revision.commit) >= 7
    assert revision.dirty is False
    (root / "untracked.txt").write_text("x")
    assert repo_revision(root).dirty is True


def test_repo_revision_without_git_uses_package_version(tmp_path: Path) -> None:
    revision = repo_revision(tmp_path)
    assert revision.commit is None
    assert (
        revision.package_version is not None
    )  # inspect_evals is not installed here; the fallback is "unknown"


def test_subject_for(tmp_path: Path) -> None:
    root = make_root(tmp_path)
    subject = subject_for("inspect_evals/stereoset", make_ctx(root, tmp_path))
    assert subject.eval == "inspect_evals/stereoset"
    assert subject.task_version is not None and subject.task_version.full == "3-A"


def test_run_id_is_unique_per_producer_target_time() -> None:
    stamp = datetime(2026, 9, 25, 4, 20, 50, tzinfo=UTC)
    assert (
        new_run_id("inspect_evals_lint", "inspect_evals/stereoset", stamp)
        == "inspect_evals_lint-20260925T042050Z-inspect-evals-stereoset"
    )


def test_skip_run(tmp_path: Path) -> None:
    root = make_root(tmp_path)
    run = skip_run(
        "inspect_dataset",
        "inspect_evals/stereoset",
        make_ctx(root, tmp_path),
        "no huggingface asset",
    )
    assert run.findings == []
    assert [(o.rule, o.status, o.message) for o in run.outcomes] == [
        ("inspect_dataset", "skip", "no huggingface asset")
    ]


def test_run_command_captures_output(tmp_path: Path) -> None:
    payload = tmp_path / "payload.txt"
    payload.write_text("hello")
    result = run_command(
        [sys.executable, str(STUBS / "echo_file.py")],
        timeout=30,
        env={"STUB_OUTPUT_FILE": str(payload)},
    )
    assert (result.returncode, result.stdout) == (0, "hello")


def test_run_command_missing_binary_raises_producer_error() -> None:
    with pytest.raises(ProducerError, match="not found"):
        run_command(["definitely-not-a-binary-xyz"], timeout=5)


def test_run_command_timeout_raises_producer_error() -> None:
    with pytest.raises(ProducerError, match="timed out"):
        run_command([sys.executable, "-c", "import time; time.sleep(5)"], timeout=0.2)
