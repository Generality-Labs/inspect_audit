"""End-to-end smoke audits on Hawk, with a cheap real model (FRICTION 81).

Each case in this folder is a whole audit (`inspect_audit/audit`) told to do a few small
steps: confirm the benchmark is installed in its box, launch a job or two (rescore, code
variant, fresh attempts, investigators), collect them, submit findings and publish. The
auditor, investigators and subjects are DeepSeek v4.1 Flash; caps are in each config.

    python tests/smoke/run_smoke.py --revision <pushed commit> [--cases simpleqa-verified ...]

Submits each case to Hawk as the logged-in operator, waits, downloads
the audit's log, and appends one JSON line per case to tests/smoke/results.jsonl: what
the audit launched and collected, how it ended, what it cost. Paid: run it only with a go.
"""

import argparse
import asyncio
import json
import os
import sys
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

HERE = Path(__file__).parent
RESULTS = HERE / "results.jsonl"
FINISHED = {"complete", "failed", "deleted", "stuck"}


def materialize(case: Path, revision: str) -> Path:
    """The case with this inspect_audit revision in place of SELF, and the shared testing
    routine (routine.md) ahead of the case's own notes: every smoke audit presses every
    button and reports on the tool, not the benchmark."""
    config = yaml.safe_load(case.read_text().replace("@SELF", f"@{revision}"))
    if os.environ.get("IA_SMOKE_KEEP_PODS"):
        # keep the runner and sandboxes for debugging; `hawk delete` afterwards
        config.setdefault("runner", {})["cleanup"] = False
    args = config["tasks"][0]["items"][0]["args"]
    args["instructions"] = (HERE / "routine.md").read_text() + "\n" + args.get("instructions", "")
    out = Path(tempfile.mkdtemp(prefix="ia-smoke-")) / case.name
    out.write_text(yaml.safe_dump(config, sort_keys=False))
    return out


API = "https://api.hawk.hawk.generalitylabs.ai"


async def submit(config: Path) -> str:
    """Submit as the logged-in operator; the audit itself in user mode (its children
    are restricted by the audit's own policy)."""
    from hawk.client import HawkClient
    from hawk.core.types import EvalSetConfig

    from inspect_audit.dispatch.hawk import HawkLogin

    login = HawkLogin(API)
    async with HawkClient(token=await login.access_token(), api_url=API) as client:
        created = await client.create_eval_set(
            EvalSetConfig.model_validate(yaml.safe_load(config.read_text())),
            refresh_token=await login.refresh_token(),
            credential_mode="user",
        )
    return str(created["eval_set_id"])


async def status(eval_set_id: str) -> str:
    from hawk.client import HawkClient

    from inspect_audit.dispatch.hawk import HawkLogin

    async with HawkClient(token=await HawkLogin(API).access_token(), api_url=API) as client:
        found = await client.get_job_status(eval_set_id)
    return str(found.status).lower() if found is not None else "unknown"


def audit_summary(log_dir: Path) -> dict[str, Any]:
    """How the audit ended, from its own log: jobs launched/collected, outcome, cost."""
    from inspect_ai.log import read_eval_log

    logs = sorted(log_dir.rglob("*.eval"))
    if not logs:
        return {"outcome": "no_log"}
    log = read_eval_log(str(logs[-1]))
    [sample] = log.samples or [None]  # type: ignore[list-item]
    if sample is None:
        return {"outcome": "no_sample", "log_status": log.status}
    store = sample.store or {}
    jobs = next((v for k, v in store.items() if k.endswith("jobs")), []) or []
    outcome = next(
        (s.value for k, s in (sample.scores or {}).items() if "audit_outcome" in k), None
    )
    cost = sum((u.total_cost or 0.0) for u in (sample.model_usage or {}).values())
    return {
        "log_status": log.status,
        "outcome": outcome or (sample.error.message[:300] if sample.error else None),
        "jobs": [
            {k: j.get(k) for k in ("label", "kind", "collected", "collection_error")} for j in jobs
        ],
        "auditor_cost_usd": round(cost, 4),
        "messages": len(sample.messages),
    }


async def run(case: Path, revision: str, wait_minutes: float) -> dict[str, Any]:
    from inspect_audit.dispatch.hawk import HawkLogin, download

    started = datetime.now(UTC).isoformat(timespec="seconds")
    row: dict[str, Any] = {
        "case": case.name.removesuffix(".eval-set.yaml"),
        "revision": revision,
        "started": started,
    }
    try:
        eval_set_id = await submit(materialize(case, revision))
        row["eval_set_id"] = eval_set_id
        print(f"{row['case']}: submitted {eval_set_id}", flush=True)
        deadline = time.monotonic() + 60 * wait_minutes
        while (state := await status(eval_set_id)) not in FINISHED:
            if time.monotonic() > deadline:
                break
            await asyncio.sleep(60)
        row["hawk_status"] = state
        out = Path(tempfile.mkdtemp(prefix="ia-smoke-logs-"))
        await download(eval_set_id, await HawkLogin(API).access_token(), out, API)
        row.update(audit_summary(out))
    except Exception as ex:  # a failed case is a result, not a crash
        row["error"] = f"{type(ex).__name__}: {ex}"[:2000]
    row["finished"] = datetime.now(UTC).isoformat(timespec="seconds")
    with RESULTS.open("a") as fh:
        fh.write(json.dumps(row) + "\n")
    return row


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    parser.add_argument("--revision", required=True, help="a pushed inspect_audit commit")
    parser.add_argument("--cases", nargs="*", help="case names (file stems); default all")
    parser.add_argument("--wait-minutes", type=float, default=180)
    args = parser.parse_args(argv)
    cases = sorted(HERE.glob("*.eval-set.yaml"))
    if args.cases:
        cases = [c for c in cases if c.name.removesuffix(".eval-set.yaml") in args.cases]
    for case in cases:
        yaml.safe_load(case.read_text())  # refuse a broken config before any spend
    failed = 0
    for case in cases:
        row = asyncio.run(run(case, args.revision, args.wait_minutes))
        print(json.dumps(row, indent=1))
        failed += bool(row.get("error")) or row.get("log_status") != "success"
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
