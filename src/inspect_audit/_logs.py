"""The declared logs: checked against the declared task, indexed, read on the host.

Logs never enter a box. An attempt is addressed by (eval_id, sample_id, epoch); the
same address in two files is refused rather than picked between. Every header must
record the declared task, the declared arguments and the installed benchmark
version -- a log of a different run is not evidence about this one.
"""

import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from importlib.metadata import PackageNotFoundError, version
from logging import getLogger
from typing import Any

from inspect_ai.event import EventTreeSpan, ModelEvent, event_tree
from inspect_ai.log import (
    EvalLog,
    read_eval_log,
    read_eval_log_sample,
    read_eval_log_sample_summaries,
)
from inspect_ai.model import ModelConfig
from inspect_ai.tool import ToolError

logger = getLogger(__name__)


class LogMismatchError(ValueError):
    """A declared log does not record the declared benchmark run."""


@dataclass(frozen=True)
class Attempt:
    """One recorded attempt at one sample."""

    log: str
    eval_id: str
    model: str
    sample_id: str
    epoch: int
    scores: dict[str, Any]
    error: str | None

    def row(self) -> dict[str, Any]:
        return {
            "eval_id": self.eval_id,
            "epoch": self.epoch,
            "model": self.model,
            "scores": self.scores,
            "error": self.error,
            "log": self.log,
        }


@dataclass
class LogIndex:
    """Every attempt in the declared logs, by sample, with the run settings they record."""

    attempts: dict[str, list[Attempt]] = field(default_factory=dict)
    judges: dict[str, str] = field(default_factory=dict)
    judge_configs: dict[str, ModelConfig] = field(default_factory=dict)
    """Model roles the recorded runs bound, role -> model; identical across logs."""
    scorer_models: set[str] = field(default_factory=set)
    """Models the recorded scorers actually called, from scoring spans."""
    eval_models: set[str] = field(default_factory=set)
    shuffled: bool = False
    """Whether any recorded run shuffled its dataset (positional ids then unstable)."""

    def for_sample(self, sample_id: str) -> list[Attempt]:
        return self.attempts.get(sample_id, [])

    def find(self, sample_id: str, eval_id: str, epoch: int) -> Attempt:
        for attempt in self.for_sample(sample_id):
            if attempt.eval_id == eval_id and attempt.epoch == epoch:
                return attempt
        raise ToolError(
            f"No recorded attempt at sample {sample_id} with eval_id {eval_id!r}, epoch {epoch}. "
            "Call attempts() for the list."
        )


def _roles(header: EvalLog) -> dict[str, str]:
    roles: dict[str, str] = {}
    for role, config in (header.eval.model_roles or {}).items():
        configs = config if isinstance(config, list) else [config]
        roles[role] = ",".join(c.model for c in configs if isinstance(c, ModelConfig))
    return roles


_COMMIT = re.compile(r"(?:^|[+.])g([0-9a-f]{7,40})(?:$|\.)")


def same_build(logged: str, installed: str) -> bool:
    """Whether two version strings name the same code.

    Equal strings do. So do two git builds naming the same commit: setuptools-scm
    writes the commit into the local version (`+g67a9ea533`), and the rest of the
    string depends on which tags the checkout happened to fetch, not on the code.
    """
    if logged == installed:
        return True
    a, b = _COMMIT.search(logged), _COMMIT.search(installed)
    if a is None or b is None:
        return False
    short = min(len(a.group(1)), len(b.group(1)))
    return a.group(1)[:short] == b.group(1)[:short]


WRAPPER = "inspect_audit/benchmark"
WRAPPED = "inspect_audit_benchmark"


def _installed(package: str) -> str:
    try:
        return version(package)
    except PackageNotFoundError:
        raise LogMismatchError(f"the benchmark package {package!r} is not installed") from None


def _check_header(
    path: str,
    header: EvalLog,
    *,
    task_name: str,
    task_args: dict[str, Any],
    package: str | None,
) -> None:
    spec = header.eval
    if spec.task_registry_name == WRAPPER:
        # the auditor's `benchmark` wrapper: its header names the wrapper, and the
        # wrapped run is recorded in the task metadata
        wrapped = (spec.metadata or {}).get(WRAPPED)
        if not isinstance(wrapped, dict):
            raise LogMismatchError(f"{path} is a benchmark-wrapper log with no recorded run")
        if wrapped.get("task") != task_name or wrapped.get("task_args") != task_args:
            raise LogMismatchError(
                f"{path} wrapped {wrapped.get('task')!r} with {wrapped.get('task_args')!r}, "
                f"not the declared {task_name!r} with {task_args!r}"
            )
        if package is not None and not same_build(str(wrapped.get("version")), _installed(package)):
            raise LogMismatchError(
                f"{path} was produced by {package} {wrapped.get('version')}; "
                f"{_installed(package)} is installed"
            )
        return
    recorded = spec.task_registry_name or spec.task
    if recorded != task_name:
        raise LogMismatchError(f"{path} records task {recorded!r}, not the declared {task_name!r}")
    for key, value in task_args.items():
        if spec.task_args.get(key) != value:
            raise LogMismatchError(
                f"{path} ran {task_name} with {key}={spec.task_args.get(key)!r}, "
                f"not the declared {value!r}"
            )
    undeclared = set(spec.task_args_passed) - set(task_args)
    if undeclared:
        raise LogMismatchError(
            f"{path} passed task argument(s) the investigation does not declare: "
            f"{', '.join(sorted(undeclared))}"
        )
    # Inspect itself decides how samples are set up and scored: a different version
    # can grade the same answer differently. Not refused (a log's exact build is often
    # unobtainable), but said, so a regrade that disagrees has a named suspect.
    logged_inspect = spec.packages.get("inspect_ai")
    if logged_inspect and not same_build(logged_inspect, version("inspect_ai")):
        logger.warning(
            f"{path} was produced by inspect_ai {logged_inspect}; this run uses "
            f"{version('inspect_ai')}: regrades may differ for that reason"
        )
    if package is not None:
        try:
            installed = version(package)
        except PackageNotFoundError:
            raise LogMismatchError(f"the benchmark package {package!r} is not installed") from None
        logged = spec.packages.get(package)
        if logged is None:
            raise LogMismatchError(f"{path} does not record which {package} version produced it")
        if not same_build(logged, installed):
            raise LogMismatchError(
                f"{path} was produced by {package} {logged}; {installed} is installed"
            )


def _scorer_models(path: str, sample_id: str | int, epoch: int) -> set[str]:
    """Models called inside scoring spans of one recorded sample."""
    sample = read_eval_log_sample(path, id=sample_id, epoch=epoch)

    def walk(nodes: Sequence[Any], scoring: bool) -> set[str]:
        found: set[str] = set()
        for node in nodes:
            if isinstance(node, EventTreeSpan):
                found |= walk(node.children, scoring or node.type in ("scorers", "scorer"))
            elif scoring and isinstance(node, ModelEvent):
                found.add(node.model)
        return found

    return walk(event_tree(sample.events or []), False)


def index_logs(
    files: Sequence[str],
    *,
    task_name: str,
    task_args: dict[str, Any],
    package: str | None,
) -> LogIndex:
    """Check every declared log against the declared run and index its attempts."""
    if not files:
        raise LogMismatchError("logs= was given but names no files")
    index = LogIndex()
    seen: dict[tuple[str, str, int], str] = {}
    roles_seen: dict[str, str] | None = None
    for path in files:
        header = read_eval_log(path, header_only=True)
        _check_header(path, header, task_name=task_name, task_args=task_args, package=package)
        roles = _roles(header)
        if roles_seen is not None and roles != roles_seen:
            raise LogMismatchError(
                f"{path} binds model roles {roles}, other logs bind {roles_seen}"
            )
        roles_seen = roles
        configs = {
            role: values[0] if isinstance(values, list) and len(values) == 1 else values
            for role, values in (header.eval.model_roles or {}).items()
        }
        if any(not isinstance(value, ModelConfig) for value in configs.values()):
            raise LogMismatchError("multi-model judge roles require explicit fan-out support")
        if index.judge_configs and index.judge_configs != configs:
            raise LogMismatchError(
                f"{path} binds different judge configurations from the other logs"
            )
        index.judge_configs = {
            role: value for role, value in configs.items() if isinstance(value, ModelConfig)
        }
        index.eval_models.add(header.eval.model)
        index.shuffled = index.shuffled or bool(header.eval.dataset.shuffled)
        summaries = read_eval_log_sample_summaries(path)
        for summary in summaries:
            key = (header.eval.eval_id, str(summary.id), summary.epoch)
            if key in seen:
                raise LogMismatchError(
                    f"attempt {key} appears in both {seen[key]} and {path}; declare one of them"
                )
            seen[key] = path
            index.attempts.setdefault(str(summary.id), []).append(
                Attempt(
                    log=path,
                    eval_id=header.eval.eval_id,
                    model=header.eval.model,
                    sample_id=str(summary.id),
                    epoch=summary.epoch,
                    scores={
                        name: {"value": score.value, "answer": score.answer}
                        for name, score in (summary.scores or {}).items()
                    },
                    error=summary.error,
                )
            )
        scored = next((s for s in summaries if s.scores), None)
        if scored is not None:
            try:
                index.scorer_models |= _scorer_models(path, scored.id, scored.epoch)
            except (IndexError, KeyError):
                raise LogMismatchError(
                    f"{path} lists {len(summaries)} samples but holds none of them (a compacted "
                    "log): it has no transcripts to investigation"
                ) from None
    index.judges = roles_seen or {}
    return index


def sliced_logs(index: LogIndex, sample_id: str) -> dict[str, bytes]:
    """This sample's recorded attempts, as Inspect logs: one per source log.

    Each keeps its source log's header (task, args, models, results for the whole run)
    and only this sample's epochs. Written in Inspect's JSON log format: Inspect reads
    it like any log, and so does plain `json`, so the box needs nothing installed.
    Written on the host; the box gets the bytes.
    """
    import tempfile

    from inspect_ai.log import write_eval_log

    by_log: dict[str, list[Attempt]] = {}
    for attempt in index.for_sample(sample_id):
        by_log.setdefault(attempt.log, []).append(attempt)
    files: dict[str, bytes] = {}
    with tempfile.TemporaryDirectory(prefix="sliced-logs-") as tmp:
        for n, (log, attempts) in enumerate(sorted(by_log.items())):
            header = read_eval_log(log, header_only=True)
            samples = [
                read_eval_log_sample(log, id=a.sample_id, epoch=a.epoch, resolve_attachments=True)
                for a in attempts
            ]
            stem = log.rstrip("/").rsplit("/", 1)[-1].removesuffix(".eval").removesuffix(".json")
            name = f"{n:02d}-{stem}.json"
            out = f"{tmp}/{name}"
            write_eval_log(header.model_copy(update={"samples": samples}), out, format="json")
            with open(out, "rb") as f:
                files[name] = f.read()
    return files
