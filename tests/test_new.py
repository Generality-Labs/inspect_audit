"""`inspect-audit-new`: an audit config from a repository, a task and models."""

import inspect
import subprocess
from pathlib import Path

import pytest
import yaml

from inspect_audit.new import NewAuditError, config, hawk_model, main


def _benchmark(path: Path) -> Path:
    """A benchmark laid out as inspect_evals is: src/, an entry point, an extra per task."""
    task = path / "src" / "toybench" / "arith"
    task.mkdir(parents=True)
    (path / "src" / "toybench" / "__init__.py").write_text("")
    (task / "__init__.py").write_text("from .arith import arith\n")
    (task / "arith.py").write_text(
        "from inspect_ai import Task, task\n\n\n@task\ndef arith() -> Task:\n    return Task()\n"
    )
    (path / "pyproject.toml").write_text(
        '[project]\nname = "toybench"\nversion = "0.1"\n'
        '[project.optional-dependencies]\narith = ["sympy"]\n'
        '[project.entry-points.inspect_ai]\ntoybench = "toybench._registry"\n'
    )
    for command in (
        ["init", "-q"],
        ["remote", "add", "origin", "git@github.com:example/toybench.git"],
        ["add", "-A"],
        ["-c", "user.name=T", "-c", "user.email=t@example.org", "commit", "-qm", "toy"],
    ):
        subprocess.run(["git", "-C", str(path), *command], check=True)
    return path


MODELS = {
    "auditor": ["openrouter/deepseek/flash"],
    "investigators": ["openrouter/deepseek/flash"],
    "subjects": ["openai/gpt-6-luna"],
    "judges": [],
}


def test_the_config_is_read_from_the_repository_at_a_pinned_commit(tmp_path: Path) -> None:
    from hawk.core.types import EvalSetConfig

    from inspect_audit import _audit

    repo = _benchmark(tmp_path / "toybench")
    built = config(
        str(repo),
        models=MODELS,
        task="arith",
        budget_usd=5.0,
        task_args={"sandbox_type": "k8s"},
        audit_revision="0" * 40,
    )
    [item] = built["tasks"][0]["items"]
    args = item["args"]
    commit = subprocess.check_output(
        ["git", "-C", str(repo), "rev-parse", "HEAD"], text=True
    ).strip()
    assert args["revision"] == commit
    assert args["repo"] == "https://github.com/example/toybench"
    assert args["target_task"] == "toybench/arith"
    assert args["source"] == "toybench.arith.arith"
    assert (
        args["task_package"]
        == f"toybench[arith] @ git+https://github.com/example/toybench@{commit}"
    )
    # spelled as our Hawk's proxy routes them (FRICTION 89)
    assert args["policy"]["models"]["auditor"] == ["openai/openrouter/deepseek/flash"]
    assert args["policy"]["models"]["subjects"] == ["openai/gpt-6-luna"]
    assert args["policy"]["limits"] == {"budget_usd": 5.0}
    assert args["task_args"] == {"sandbox_type": "k8s"}
    # runners install a commit of inspect_audit, never a moving branch
    assert built["packages"][1].endswith("@" + "0" * 40)
    # Hawk accepts it, and every argument is one the audit task takes
    EvalSetConfig.model_validate(built)
    assert set(args) <= set(inspect.signature(inspect.unwrap(_audit.audit)).parameters)


def test_the_only_task_needs_no_name_and_models_have_no_defaults(tmp_path: Path) -> None:
    repo = _benchmark(tmp_path / "toybench")
    built = config(str(repo), models=MODELS, audit_revision="0" * 40)
    assert built["tasks"][0]["items"][0]["args"]["target_task"] == "toybench/arith"
    with pytest.raises(NewAuditError, match="no defaults"):
        config(str(repo), models={"auditor": [], "investigators": []})
    with pytest.raises(NewAuditError, match="no @task named"):
        config(str(repo), models=MODELS, task="missing")


def test_the_command_prints_yaml_and_says_what_is_missing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _benchmark(tmp_path / "toybench")
    assert (
        main([str(repo), "--models", "openrouter/deepseek/flash", "--audit-revision", "0" * 40])
        == 0
    )
    printed = yaml.safe_load(capsys.readouterr().out)
    assert printed["tasks"][0]["items"][0]["args"]["source"] == "toybench.arith.arith"
    assert main([str(repo)]) == 2
    assert "no defaults" in capsys.readouterr().err


def test_hawk_spelling() -> None:
    assert hawk_model("openrouter/anthropic/claude-x") == "openai/openrouter/anthropic/claude-x"
    assert hawk_model("anthropic/claude-x") == "anthropic/claude-x"
