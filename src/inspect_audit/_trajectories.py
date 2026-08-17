"""Materialising the actual trajectories behind a sample's attempts.

A graded answer says whether a model got it right; only the trajectory says *how*.
Most of what an auditor needs to notice — a tool that kept failing, an agent that
read the answer out of the environment, a run cut off mid-progress, a refusal
scored as incapability — is invisible in the output and obvious in the transcript.

So each model gets one file, holding a JSON array of the native `EvalSample`s it
produced for this sample — one element per epoch. That keeps the directory reading as
"one file per model", which is how an auditor thinks about the field, while repeated
epochs of the same model stay together where their consistency is visible. Each
element is lossless and round-trips through `EvalSample.model_validate_json()`, and
since the audited task's packages are installed in the sandbox the auditor can read
them with the real API rather than parsing by hand.

Whole `.eval` files are deliberately not copied: a log is a container for a thousand
samples, and a case needs one of them. We extract the slice, not the container.
"""

import logging
import re
import tempfile
from pathlib import Path

from inspect_ai.log import read_eval_log_sample

from ._case import AUDIT_ROOT, AttemptRef

logger = logging.getLogger(__name__)

__all__ = ["trajectory_files"]

_UNSAFE = re.compile(r"[^A-Za-z0-9._-]+")


def _slug(model: str) -> str:
    """`openrouter/anthropic/claude-haiku-4.5` -> `anthropic-claude-haiku-4.5`."""
    parts = [p for p in model.split("/") if p]
    return _UNSAFE.sub("-", "-".join(parts[-2:] if len(parts) > 1 else parts)).strip("-")


def trajectory_files(
    attempts: list[AttemptRef],
    *,
    stage: Path | None = None,
    exclude_fields: set[str] | None = None,
) -> dict[str, str]:
    """Write one trajectory file per model; return `Sample.files` entries.

    Values are *paths*, not contents: Inspect copies a path into the sandbox
    without the bytes travelling through the sample, which keeps large trajectories
    out of the audit log.

    Args:
        attempts: The attempts to fetch, as recorded by `candidates`.
        stage: Directory to stage files in (a temporary one by default).
        exclude_fields: Sample fields to drop when reading, for very large logs.

    Returns:
        Mapping of sandbox path -> host path, one entry per model.
    """
    if not attempts:
        return {}

    staging = stage or Path(tempfile.mkdtemp(prefix="inspect_audit_traj_"))
    staging.mkdir(parents=True, exist_ok=True)

    by_model: dict[str, list[str]] = {}
    for attempt in sorted(attempts, key=lambda a: (a.model, a.epoch)):
        try:
            sample = read_eval_log_sample(
                attempt.log_file,
                id=attempt.sample_id,
                epoch=attempt.epoch,
                exclude_fields=exclude_fields,
            )
        except Exception as ex:  # noqa: BLE001 - one bad attempt must not fail the case
            # An unreadable attempt costs the auditor one trajectory, not the whole
            # case, but it is never silent: a missing trajectory changes what the
            # auditor can conclude.
            logger.warning(
                "could not read attempt %s epoch %s from %s: %s: %s",
                attempt.sample_id, attempt.epoch, attempt.log_file, type(ex).__name__, ex,
            )
            continue

        by_model.setdefault(_slug(attempt.model), []).append(sample.model_dump_json(indent=2))

    files: dict[str, str] = {}
    for slug, epochs in by_model.items():
        host = staging / f"{slug}.json"
        host.write_text("[\n" + ",\n".join(epochs) + "\n]\n", encoding="utf-8")
        files[f"{AUDIT_ROOT}/attempts/{slug}.json"] = str(host)

    return files
