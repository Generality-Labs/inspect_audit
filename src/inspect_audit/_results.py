"""Investigators' verdicts as Inspect Scout results: the one record of them (FRICTION 102).

An investigation job's log holds each verdict as the score its check's scorer recorded.
When the job is collected, a Scout scan over its logs turns every verdict into a Scout
`Result`, written by Scout itself: one table per check (`<check>.parquet`, Scout spells
`-` as `_`), one row per investigated item, on the transcript of the investigator's own
run, so Scout's own columns say which benchmark, sample, epoch and model it was, and
`scout view` shows each verdict beside the investigation that reached it. The register
is a view over these tables (`investigator_takes`), never a second store of them.
"""

import json
from pathlib import Path
from typing import Any, cast

from inspect_ai.log import read_eval_log_sample_async
from pydantic import JsonValue

RESULTS = "results"  # under the audit root: one Scout scan directory per collected job


def table(check: str) -> str:
    """The name Scout gives a check's results table."""
    return check.replace("-", "_")


def _scanner(check: str) -> Any:
    """A scanner that calls no model: the verdict the investigator recorded, as a Result."""
    from inspect_scout import Result, Scanner, Transcript, scanner

    # Scout requires a content selection; the verdict itself is read from the log, where
    # the check's scorer recorded it with its explanation, evidence and detail fields
    @scanner(name=table(check), messages=["assistant"], metadata=True)
    def verdict() -> Scanner[Transcript]:
        async def scan(transcript: Transcript) -> Result:
            meta = transcript.metadata or {}
            if not transcript.source_uri:
                return Result(value=None, explanation="the transcript names no log")
            sample = await read_eval_log_sample_async(
                transcript.source_uri, meta["id"], meta["epoch"]
            )
            score = (sample.scores or {}).get(check)
            if score is None:
                # a scanner always answers: no value is "this job did not run the check"
                return Result(value=None, explanation=f"this investigation did not run {check}")
            return Result(
                value=cast(JsonValue, score.value),
                explanation=score.explanation,
                label="investigator",
                metadata=dict(score.metadata or {}),
            )

        return scan

    return verdict()


async def record(logs: list[Path], root: Path, checks: list[str], address: str) -> Path:
    """Scan a collected investigation job's logs into Scout results; returns the scan."""
    from inspect_scout import transcripts_from
    from inspect_scout.aio import scan_async

    status = await scan_async(
        [_scanner(check) for check in checks],
        transcripts=transcripts_from([str(log) for log in logs]),
        scans=str(root / RESULTS),
        # the job's address on every row: where the investigation itself can be read
        metadata={"address": address},
        # in this process: a handful of small logs, and Scout's worker processes would
        # be spawned from inside the auditor's own run
        max_processes=1,
        fail_on_error=True,
    )
    if not status.complete:
        raise ValueError(f"the Scout scan of {address} did not complete: {status.location}")
    return Path(status.location)


async def investigator_takes(root: Path, checks: list[str]) -> list[dict[str, Any]]:
    """Every investigator verdict recorded in this audit's results, oldest scan first.

    One dict per row: sample, check, the verdict's label and explanation, its evidence
    and detail fields, and where it came from (job address, eval, epoch, model).
    """
    from inspect_scout.aio import scan_results_df_async

    found: list[dict[str, Any]] = []
    folder = root / RESULTS
    for location in sorted(folder.glob("scan_id=*"), key=lambda p: p.stat().st_mtime):
        results = await scan_results_df_async(str(location))
        for check in checks:
            frame = results.scanners.get(table(check)) if table(check) in results.scanners else None
            if frame is None:
                continue
            for row in frame.to_dict("records"):
                if row.get("value") is None:
                    continue
                scan_meta = _json(row.get("scan_metadata"))
                found.append(
                    {
                        "sample": str(row["transcript_task_id"]),
                        "check": check,
                        "label": str(row["value"]),
                        "explanation": str(row.get("explanation") or ""),
                        "metadata": _json(row.get("metadata")),
                        "run": scan_meta.get("address"),
                        "eval_id": row.get("transcript_source_id"),
                        "epoch": row.get("transcript_task_repeat"),
                        "model": row.get("transcript_model"),
                        "result": row.get("uuid"),
                    }
                )
    return found


def _json(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value:
        loaded = json.loads(value)
        return loaded if isinstance(loaded, dict) else {}
    return {}
