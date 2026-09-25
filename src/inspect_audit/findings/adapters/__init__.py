"""What every adapter shares: the run context, subject derivation, and a command runner that reports rather than raises."""

from __future__ import annotations

import os
import re
import subprocess
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any

import yaml

from ..models import Outcome, Revision, Run, Subject, TaskVersion, utcnow
from ..producers import ProducerConfig


@dataclass
class Context:
    """Everything an adapter needs beyond the target name."""

    ie_root: Path
    logs: list[Path] = field(default_factory=list)
    out_dir: Path = Path("findings-out")
    producers: ProducerConfig = field(default_factory=ProducerConfig)
    resolve: bool = False


class ProducerError(Exception):
    """A producer could not be run to completion."""


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    stdout: str
    stderr: str


def run_command(
    argv: Sequence[str],
    *,
    timeout: float,
    cwd: Path | None = None,
    env: Mapping[str, str] | None = None,
) -> CommandResult:
    """Run a producer and capture its output. Raises ProducerError for a missing binary or a timeout."""
    merged = {**os.environ, **(env or {})}
    try:
        completed = subprocess.run(
            list(argv), capture_output=True, text=True, timeout=timeout, cwd=cwd, env=merged, check=False
        )
    except FileNotFoundError as ex:
        raise ProducerError(f"producer binary not found: {argv[0]}") from ex
    except subprocess.TimeoutExpired as ex:
        raise ProducerError(f"producer timed out after {timeout:g}s: {' '.join(argv[:3])}") from ex
    return CommandResult(completed.returncode, completed.stdout, completed.stderr)


def package_of(target: str) -> str:
    """`inspect_evals/stereoset` -> `stereoset`."""
    return target.rsplit("/", 1)[-1]


def slug(text: str) -> str:
    return re.sub(r"-+", "-", re.sub(r"[^a-z0-9-]", "-", text.lower())).strip("-")


def eval_yaml(ie_root: Path, target: str) -> dict[str, Any]:
    """The eval's `eval.yaml` as a dict, or `{}` when absent or unreadable."""
    path = ie_root / "src" / "inspect_evals" / package_of(target) / "eval.yaml"
    try:
        loaded = yaml.safe_load(path.read_text())
    except (OSError, yaml.YAMLError):
        return {}
    return loaded if isinstance(loaded, dict) else {}


def task_version_from_yaml(data: Mapping[str, Any]) -> TaskVersion | None:
    raw = data.get("version")
    return TaskVersion.parse(str(raw)) if raw is not None else None


def _git(ie_root: Path, *args: str) -> str | None:
    try:
        completed = subprocess.run(
            ["git", "-C", str(ie_root), *args], capture_output=True, text=True, timeout=30, check=False
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return completed.stdout.strip() if completed.returncode == 0 else None


def repo_revision(ie_root: Path) -> Revision:
    """Commit and dirtiness from git; installed inspect_evals version as the fallback identity."""
    commit = _git(ie_root, "rev-parse", "HEAD")
    status = _git(ie_root, "status", "--porcelain")
    try:
        package_version: str | None = version("inspect_evals")
    except PackageNotFoundError:
        package_version = None
    if commit is None and package_version is None:
        package_version = "unknown"
    return Revision(commit=commit, package_version=package_version, dirty=None if status is None else bool(status))


def subject_for(target: str, ctx: Context) -> Subject:
    return Subject(
        eval=target,
        revision=repo_revision(ctx.ie_root),
        task_version=task_version_from_yaml(eval_yaml(ctx.ie_root, target)),
    )


def new_run_id(producer: str, target: str, timestamp: datetime) -> str:
    return f"{producer}-{timestamp.strftime('%Y%m%dT%H%M%SZ')}-{slug(target)}"


def skip_run(
    producer: str, target: str, ctx: Context, message: str, *, timestamp: datetime | None = None
) -> Run:
    """A run that could not happen: one skip outcome carrying the reason, no findings."""
    stamp = timestamp or utcnow()
    return Run(
        id=new_run_id(producer, target, stamp),
        timestamp=stamp,
        producer=producer,
        subject=subject_for(target, ctx),
        outcomes=[Outcome(rule=producer, status="skip", message=message[-2000:])],
    )
