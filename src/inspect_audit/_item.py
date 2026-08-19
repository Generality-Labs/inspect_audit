import json
from collections import defaultdict
from logging import getLogger
from pathlib import Path
from typing import Any

from inspect_ai import Task
from inspect_ai._eval.task.util import task_run_dir
from inspect_ai.dataset import Sample
from inspect_ai.log import (
    EvalSample,
    read_eval_log,
    read_eval_log_sample,
    write_eval_log,
)
from inspect_ai.util import SandboxEnvironmentType, resource
from inspect_ai.util._sandbox.environment import resolve_sandbox_environment
from pydantic import BaseModel, Field

from ._sandbox import BENCHMARK_SERVICE

logger = getLogger(__name__)

AUDIT_ROOT = "/audit"

GRADING_TEMPLATE = Path(__file__).parent / "templates" / "grading.md"


class AttemptRef(BaseModel):
    """One recorded attempt at an item: which log holds it, and how it scored."""

    model: str
    epoch: int
    scores: dict[str, str] = Field(default_factory=dict)
    log_file: str
    sample_id: str | int


class AuditItem(BaseModel):
    """The item an audit sample is auditing, carried in its `Sample.metadata`."""

    task: str
    task_args: dict[str, Any] = Field(default_factory=dict)
    sample_id: str | int
    attempts: list[AttemptRef] = Field(default_factory=list)


def item_sample(
    task: Task,
    sample: Sample,
    item: AuditItem,
    *,
    prompt: str,
    stage: Path,
    sandbox: SandboxEnvironmentType | None = None,
    original_env: SandboxEnvironmentType | None = None,
    benchmark: bool = False,
) -> Sample:
    """One audited item, as an Inspect `Sample`."""
    files = item_files(task, sample, item.attempts, stage=stage, original_env=original_env)
    metadata: dict[str, Any] = {"audit_item": item.model_dump()}

    # the benchmark's environment is image plus per-sample state: forward the
    # original sample's files into the benchmark service, and carry its setup
    # script for `benchmark_setup` to run there
    if benchmark:
        run_dir = Path(task_run_dir(task))
        for name, value in (sample.files or {}).items():
            files[f"{BENCHMARK_SERVICE}:{name}"] = _anchored(value, run_dir)
        if sample.setup is not None:
            setup = _anchored(sample.setup, run_dir)
            path = Path(setup)
            metadata["benchmark_setup"] = path.read_text() if path.is_file() else setup
        # the benchmark's own sample metadata, for its grader (base_commit etc.);
        # carried on the audit sample, not shown to the auditor
        metadata["benchmark_metadata"] = dict(sample.metadata or {})

    return Sample(
        id=str(item.sample_id),
        input=prompt,
        target=sample.target,
        metadata=metadata,
        sandbox=sandbox,
        files=files,
    )


def _anchored(value: str, run_dir: Path) -> str:
    # relative file values resolve against the audited task's directory, not ours
    if not value.startswith("/") and (run_dir / value).is_file():
        return str(run_dir / value)
    return value


def item_files(
    task: Task,
    sample: Sample,
    attempts: list[AttemptRef],
    *,
    stage: Path,
    original_env: SandboxEnvironmentType | None = None,
) -> dict[str, str]:
    """Stage one item's files on the host and return its `Sample.files` mapping.

    Args:
        task: The task being audited.
        sample: The sample being audited.
        attempts: The recorded attempts at this sample.
        stage: Directory to stage this item's files in.
        original_env: The audited task's own sandbox definition, staged verbatim.
    """
    stage.mkdir(parents=True, exist_ok=True)
    files: dict[str, str] = {}

    # values must be host paths: inspect resolves a contents-shaped value as a data
    # uri, then a url, then a file at that path
    def staged(name: str, content: str) -> None:
        host = stage / name
        host.parent.mkdir(parents=True, exist_ok=True)
        host.write_text(content, encoding="utf-8")
        files[f"{AUDIT_ROOT}/{name}"] = str(host)

    # the sample in inspect's own shape, one-record dataset
    record = sample.model_dump(exclude_none=True, exclude={"files", "sandbox", "setup"})
    staged("sample.json", json.dumps([record], indent=2, default=str))

    # where grading lives and how to read it
    staged("gold/grading.md", grading_doc(task, sample))

    # the environment's own definition and the sliced logs
    files.update(env_files(original_env, stage=stage / "env"))
    files.update(sample_logs(attempts, stage=stage / "logs"))
    return files


def sample_logs(attempts: list[AttemptRef], *, stage: Path) -> dict[str, str]:
    """Write one real `.eval` per source log, sliced to this item's attempts.

    Headers are kept verbatim so the auditor can check how attempts were graded and
    elicited against the log itself rather than a summary of ours.
    """
    if not attempts:
        return {}

    by_log: dict[str, list[AttemptRef]] = defaultdict(list)
    for attempt in attempts:
        by_log[attempt.log_file].append(attempt)

    stage.mkdir(parents=True, exist_ok=True)
    files: dict[str, str] = {}
    for log_file, refs in sorted(by_log.items()):
        # read the header and just this item's samples
        try:
            log = read_eval_log(log_file, header_only=True)
            samples: list[EvalSample] = [
                read_eval_log_sample(log_file, id=ref.sample_id, epoch=ref.epoch)
                for ref in sorted(refs, key=lambda r: r.epoch)
            ]
        except Exception as ex:
            # a missing log costs the auditor one log's attempts, not the whole item
            logger.warning(
                "could not slice %s for sample %s: %s: %s",
                log_file, refs[0].sample_id, type(ex).__name__, ex,
            )
            continue

        # narrow the sample list, keep the source log's own filename
        log.samples = samples
        host = stage / Path(log_file.replace("file://", "")).name
        write_eval_log(log, str(host))
        files[f"{AUDIT_ROOT}/logs/{host.name}"] = str(host)

    return files


def env_files(spec: SandboxEnvironmentType | None, *, stage: Path) -> dict[str, str]:
    """Stage the environment's own definition for the auditor to read."""
    resolved = resolve_sandbox_environment(spec)
    if resolved is None or resolved.config is None:
        return {}

    stage.mkdir(parents=True, exist_ok=True)
    if isinstance(resolved.config, str):
        source = Path(resolved.config)
        if not source.is_file():
            return {}
        host = stage / source.name
        host.write_bytes(source.read_bytes())
    else:
        # a config object rather than a file (some sandbox providers configure inline)
        host = stage / "sandbox.json"
        host.write_text(resolved.config.model_dump_json(indent=2), encoding="utf-8")

    return {f"{AUDIT_ROOT}/env/{host.name}": str(host)}


def grading_doc(task: Task, sample: Sample) -> str:
    """Render `gold/grading.md` for one item."""
    # each scorer as (qualified name, module); the module is how the auditor finds the code
    scorers = task.scorer if isinstance(task.scorer, list) else [task.scorer]
    named: list[tuple[str, str]] = []
    for scorer in scorers:
        if scorer is None:
            continue
        fn = getattr(scorer, "__wrapped__", scorer)
        named.append(
            (
                getattr(fn, "__qualname__", getattr(fn, "__name__", "?")),
                getattr(fn, "__module__", "?"),
            )
        )

    return resource(str(GRADING_TEMPLATE), type="file").format(
        scorers="\n".join(f"- `{name}`, defined in `{module}`" for name, module in named)
        or "- not recovered",
        modules=" ".join(sorted({module for _, module in named})) or "?",
        metadata_keys=(
            ", ".join(f"`{k}`" for k in sorted((sample.metadata or {}).keys())) or "(none)"
        ),
    )
