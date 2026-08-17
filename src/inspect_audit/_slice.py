"""Slicing the recorded attempts at one sample out of the logs that hold them.

The output is real `.eval` logs, not a summary of them. Each source log becomes one
log in the case, keeping its own filename, its header **verbatim**, and only the
samples that answer the item being audited.

This is the whole point of the design. A header already carries what graded these
attempts (`eval.scorers[].options`), how the model was elicited (`plan.steps`,
`plan.config`, `eval.config`, `eval.model_args`), and what was installed
(`eval.packages`). Deriving our own summary of those facts would hand the auditor a
private schema it has to trust; handing it the log lets it use `read_eval_log`,
`samples_df`, and the viewer against the real thing, and check anything we claim.

Whole logs are still not copied: a log is a container for a thousand samples and a
case needs one of them. We take the slice and keep the container's header.
"""

import logging
from collections import defaultdict
from pathlib import Path

from inspect_ai.log import (
    EvalSample,
    read_eval_log,
    read_eval_log_sample,
    write_eval_log,
)

from ._case import AUDIT_ROOT, AttemptRef

logger = logging.getLogger(__name__)

__all__ = ["sample_logs"]


def sample_logs(attempts: list[AttemptRef], *, stage: Path) -> dict[str, str]:
    """Write one real `.eval` per source log, holding just this item's attempts.

    Args:
        attempts: The attempts to slice, as recorded by `candidates`.
        stage: Directory to write the sliced logs into.

    Returns:
        Mapping of sandbox path -> host path, one entry per source log. Values are
        paths, so Inspect copies the file into the sandbox without the bytes
        travelling through the sample (and so without entering the audit log).
    """
    if not attempts:
        return {}

    by_log: dict[str, list[AttemptRef]] = defaultdict(list)
    for attempt in attempts:
        by_log[attempt.log_file].append(attempt)

    stage.mkdir(parents=True, exist_ok=True)
    files: dict[str, str] = {}

    for log_file, refs in sorted(by_log.items()):
        try:
            log = read_eval_log(log_file, header_only=True)
            samples: list[EvalSample] = [
                read_eval_log_sample(log_file, id=ref.sample_id, epoch=ref.epoch)
                for ref in sorted(refs, key=lambda r: r.epoch)
            ]
        except Exception as ex:  # noqa: BLE001 - one bad log must not fail the case
            # A missing log costs the auditor one model's attempts, not the whole
            # case, but it is never silent: what the auditor cannot see changes what
            # it is entitled to conclude.
            logger.warning(
                "could not slice %s for sample %s: %s: %s",
                log_file, refs[0].sample_id, type(ex).__name__, ex,
            )
            continue

        # Header untouched; only the sample list narrowed.
        log.samples = samples
        host = stage / _log_name(log_file)
        write_eval_log(log, str(host))
        files[f"{AUDIT_ROOT}/logs/{host.name}"] = str(host)

    return files


def _log_name(log_file: str) -> str:
    """The source log's own filename, so the case reads like the logs it came from."""
    return Path(log_file.replace("file://", "")).name
