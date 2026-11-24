"""Validate declared logs and index attempts by (eval_id, sample_id, epoch).

Reject duplicate attempts and mismatched task, arguments or benchmark versions.
Read native logs on the host; export selected content through tools.
"""

import inspect
import re
from collections.abc import Collection, Sequence
from dataclasses import dataclass, field
from importlib.metadata import PackageNotFoundError, version
from logging import getLogger
from pathlib import Path, PurePosixPath
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
    provenance: list[str] = field(default_factory=list)
    """How the logs' recorded benchmark build differs from the installed one; empty when
    it matches. Said to the investigators and on the page, never a refusal."""

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
_DIRTY = re.compile(r"\.d\d{8}$")


def same_build(logged: str, installed: str) -> bool:
    """Whether two version strings name the same code.

    Equal strings do. So do two git builds naming the same commit: setuptools-scm
    writes the commit into the local version (`+g67a9ea533`), and the rest of the
    string depends on which tags the checkout happened to fetch, not on the code.
    A build from an edited tree (setuptools-scm's `.dYYYYMMDD`) names no commit's code,
    so it matches nothing but itself.
    """
    if logged == installed:
        return True
    if _DIRTY.search(logged) or _DIRTY.search(installed):
        return False
    a, b = _COMMIT.search(logged), _COMMIT.search(installed)
    if a is None or b is None:
        return False
    short = min(len(a.group(1)), len(b.group(1)))
    return a.group(1)[:short] == b.group(1)[:short]


WRAPPER = "inspect_audit/benchmark"
WRAPPED = "inspect_audit_benchmark"


def distribution_of(task_name: str) -> str | None:
    """The distribution that provides a registry task, from the task's namespace.

    `inspect_evals/simpleqa` is provided by the distribution `inspect_evals`, and the
    two names coincide; `bench/simpleqa_verified_anti_abstention` is provided by
    `epoch-bench`, and the namespace alone is not a distribution anyone installed.
    The import name maps to its distribution through the installed metadata; a
    namespace the metadata does not know is taken as the distribution name.
    """
    if "/" not in task_name:
        return None
    namespace = task_name.split("/")[0]
    from importlib.metadata import packages_distributions

    known = packages_distributions().get(namespace)
    return known[0] if known else namespace


def _build_notice(package: str, logged: str | None, recorded: dict[str, str]) -> str | None:
    """How a log's recorded benchmark build differs from the installed one, or None.

    A different build is not refused: an Epoch review regrades Epoch's logs with a
    reconstruction of a private package, and the point of the review is to look for
    problems in them. It is said, on every job and on the page, so a regrade that
    disagrees with a recorded grade has the build difference as a named suspect.
    """
    try:
        installed = version(package)
    except PackageNotFoundError:
        return (
            f"no distribution named {package!r} is installed here, so the build that "
            "produced the log could not be compared with the one regrading it"
        )
    if logged is None:
        others = ", ".join(f"{n} {v}" for n, v in sorted(recorded.items()) if n != "inspect_ai")
        return (
            f"produced without recording a {package} version"
            + (f" (its recorded packages: {others})" if others else "")
            + f"; {package} {installed} is installed, so the recorded grades come from a "
            "different build of the benchmark"
        )
    if same_build(logged, installed):
        return None
    return (
        f"produced by {package} {logged}; {package} {installed} is installed, so the "
        "recorded grades come from a different build of the benchmark"
    )


def _same_task(recorded: str, task_file: str | None, task_name: str) -> bool:
    """Whether a log's task is the declared one.

    A run started from a file (`inspect eval src/cvebench/cvebench.py`) records the
    bare function name and the file, not the package's registry name
    (`cvebench/cvebench`). That is the same task when the declared task's function has
    the recorded name and is defined in a file at the recorded path (its last two parts:
    the package folder and the module, which an install keeps).
    """
    if recorded == task_name:
        return True
    if not task_file or recorded != task_name.rsplit("/", 1)[-1]:
        return False
    from inspect_ai._util.registry import registry_lookup

    try:
        source = inspect.getsourcefile(inspect.unwrap(registry_lookup("task", task_name)))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return False
    if source is None:
        return False
    logged, declared = PurePosixPath(task_file).parts, Path(source).resolve().parts
    return len(logged) >= 2 and declared[-2:] == logged[-2:]


def _binding(args: dict[str, Any], sample_selecting: Collection[str]) -> dict[str, Any]:
    """The arguments a log must share with the declaration: all but the sample-selecting ones."""
    return {key: value for key, value in args.items() if key not in sample_selecting}


def _check_header(
    path: str,
    header: EvalLog,
    *,
    task_name: str,
    task_args: dict[str, Any],
    package: str | None,
    sample_selecting_args: Collection[str] = (),
) -> str | None:
    """Refuse a log of another task or other task arguments; say when the build differs.

    `sample_selecting_args` name arguments that only choose which samples run (e.g.
    ExploitBench's `vulnerability_ids`, one CVE per log): a log may record any value
    for them, as its items are still checked one by one against the installed dataset
    (`check_same_items`). Every other argument binds.

    The returned notice names the difference; None when the build matches or no
    package was named.
    """
    task_args = _binding(task_args, sample_selecting_args)
    spec = header.eval
    if spec.task_registry_name == WRAPPER:
        # the auditor's `benchmark` wrapper: its header names the wrapper, and the
        # wrapped run is recorded in the task metadata
        wrapped = (spec.metadata or {}).get(WRAPPED)
        if not isinstance(wrapped, dict):
            raise LogMismatchError(f"{path} is a benchmark-wrapper log with no recorded run")
        recorded_args = wrapped.get("task_args")
        if isinstance(recorded_args, dict):
            recorded_args = _binding(recorded_args, sample_selecting_args)
        if wrapped.get("task") != task_name or recorded_args != task_args:
            raise LogMismatchError(
                f"{path} wrapped {wrapped.get('task')!r} with {wrapped.get('task_args')!r}, "
                f"not the declared {task_name!r} with {task_args!r}"
            )
        if package is None:
            return None
        recorded_version = wrapped.get("version")
        return _build_notice(package, str(recorded_version) if recorded_version else None, {})
    recorded = spec.task_registry_name or spec.task
    if not _same_task(recorded, spec.task_file, task_name):
        raise LogMismatchError(f"{path} records task {recorded!r}, not the declared {task_name!r}")
    for key, value in task_args.items():
        if spec.task_args.get(key) != value:
            raise LogMismatchError(
                f"{path} ran {task_name} with {key}={spec.task_args.get(key)!r}, "
                f"not the declared {value!r}"
            )
    undeclared = set(spec.task_args_passed) - set(task_args) - set(sample_selecting_args)
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
    if package is None:
        return None
    return _build_notice(package, spec.packages.get(package), dict(spec.packages))


def _aggregate(notices: dict[str, int], total: int) -> list[str]:
    """One line per distinct notice, with how many of the logs it covers."""
    return [
        f"{count} of {total} log{'s' if total != 1 else ''} {notice}"
        for notice, count in notices.items()
    ]


def provenance_of(
    files: Sequence[str],
    *,
    task_name: str,
    task_args: dict[str, Any],
    package: str | None,
    sample_selecting_args: Collection[str] = (),
) -> list[str]:
    """What the logs say about the build that produced them, against the installed one.

    The header check alone, for a host that stages the logs without indexing their
    attempts (the audit, at its start); the same notices `index_logs` records.
    """
    notices: dict[str, int] = {}
    for path in files:
        header = read_eval_log(path, header_only=True)
        notice = _check_header(
            path,
            header,
            task_name=task_name,
            task_args=task_args,
            package=package,
            sample_selecting_args=sample_selecting_args,
        )
        if notice:
            notices[notice] = notices.get(notice, 0) + 1
    return _aggregate(notices, len(files))


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
    sample_selecting_args: Collection[str] = (),
) -> LogIndex:
    """Check every declared log against the declared run and index its attempts."""
    if not files:
        raise LogMismatchError("logs= was given but names no files")
    index = LogIndex()
    seen: dict[tuple[str, str, int], str] = {}
    roles_seen: dict[str, str] | None = None
    notices: dict[str, int] = {}
    for path in files:
        header = read_eval_log(path, header_only=True)
        notice = _check_header(
            path,
            header,
            task_name=task_name,
            task_args=task_args,
            package=package,
            sample_selecting_args=sample_selecting_args,
        )
        if notice:
            notices[notice] = notices.get(notice, 0) + 1
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
                    "log): it has no transcripts to investigate"
                ) from None
    index.judges = roles_seen or {}
    index.provenance = _aggregate(notices, len(files))
    for line in index.provenance:
        logger.warning("supplied logs: %s", line)
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
