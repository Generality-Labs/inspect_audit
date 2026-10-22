"""Run pinned API-free compatibility cases on Hawk and retain native log evidence."""

import argparse
import asyncio
import json
import os
import re
import subprocess
import time
from pathlib import Path
from typing import Any

import yaml
from hawk.client import HawkClient
from inspect_ai.event import ModelEvent
from inspect_ai.log import read_eval_log

from inspect_audit._runs import HawkLogin

ROOT = Path(__file__).resolve().parents[2]
CASES = Path(__file__).parent / "cases"
REPO = "https://github.com/Generality-Labs/inspect_audit"


def materialize(config: dict[str, Any], revision: str) -> dict[str, Any]:
    """Test this IA revision while retaining every benchmark's own pinned version."""
    encoded = json.dumps(config)
    encoded = re.sub(re.escape(REPO) + r"@(?:SELF|[0-9a-f]{40})", f"{REPO}@{revision}", encoded)
    return json.loads(encoded)


def summarize(paths: list[Path]) -> dict[str, Any]:
    samples: list[dict[str, Any]] = []
    failures: list[str] = []
    for path in paths:
        log = read_eval_log(path)
        if log.error:
            failures.append(log.error.message)
        for sample in log.samples or []:
            score = next(
                (
                    s
                    for k, s in (sample.scores or {}).items()
                    if k.split("/")[-1] == "compatibility"
                ),
                None,
            )
            result = (
                dict(score.metadata or {})
                if score
                else {
                    "status": "failed",
                    "stages": {},
                    "errors": {"probe": "No compatibility score recorded"},
                }
            )
            external = sorted(
                {
                    e.model
                    for e in sample.events
                    if isinstance(e, ModelEvent) and not e.model.startswith("ia_probe/")
                }
            )
            if sample.error or sample.limit or external:
                result["status"] = "failed"
            samples.append(
                {
                    "sample": str(sample.id),
                    **result,
                    "external_models": external,
                    "error": sample.error.message if sample.error else None,
                    "limit": sample.limit.type if sample.limit else None,
                }
            )
    status = (
        "failed"
        if failures or not samples or any(s["status"] == "failed" for s in samples)
        else "partial"
        if any(s["status"] == "partial" for s in samples)
        else "passed"
    )
    return {"status": status, "samples": samples, "errors": failures}


def write_results(output: Path, rows: list[dict[str, Any]]) -> None:
    output.mkdir(parents=True, exist_ok=True)
    (output / "summary.json").write_text(json.dumps(rows, indent=2) + "\n")
    lines = [
        "# Inspect Auditor compatibility",
        "",
        "These are functionality probes, not scientific validity assessments. `partial` means a model judge was intentionally disabled.",
        "",
        "| Case | Result | Stages | Hawk |",
        "|---|---|---|---|",
    ]
    for row in rows:
        stages = "; ".join(
            f"{k}: {v}" for s in row.get("samples", []) for k, v in s.get("stages", {}).items()
        ) or row.get(
            "reason",
            "Sample failed before compatibility score; inspect native log"
            if any(s.get("error") for s in row.get("samples", []))
            else "No sample result",
        )
        job = row.get("job")
        link = f"[run]({row['viewer']}/eval-set/{job})" if job else "—"
        lines.append(
            f"| {row['case']} | {row['status']} | {stages.replace('|', '/').replace(chr(10), ' ')} | {link} |"
        )
    (output / "summary.md").write_text("\n".join(lines) + "\n")


async def run(args: argparse.Namespace) -> int:
    output = args.output.resolve()
    revision = (
        args.ref
        or (
            await asyncio.to_thread(
                subprocess.check_output, ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
            )
        ).strip()
    )
    if not re.fullmatch(r"[0-9a-f]{40}", revision):
        raise ValueError("--ref must be a full published commit SHA")
    files = sorted(CASES.glob("*.eval-set.yaml"))
    if args.cases:
        wanted = set(args.cases.split(","))
        files = [p for p in files if p.name.removesuffix(".eval-set.yaml") in wanted]
        if wanted - {p.name.removesuffix(".eval-set.yaml") for p in files}:
            raise ValueError("Unknown compatibility case")
    if not files:
        raise ValueError("No compatibility cases selected")
    if args.prepare_only:
        from hawk.core.types import EvalSetConfig

        for path in files:
            config = materialize(yaml.safe_load(path.read_text()), revision)
            EvalSetConfig.model_validate(config)
            directory = output / path.name.removesuffix(".eval-set.yaml")
            directory.mkdir(parents=True, exist_ok=True)
            (directory / "config.yaml").write_text(yaml.safe_dump(config, sort_keys=False))
        print(f"Prepared and validated {len(files)} configs at {revision}")
        return 0
    api = os.environ.get("HAWK_API_URL")
    if not api:
        raise ValueError("HAWK_API_URL is required")
    login = HawkLogin(api)
    # Authenticate once before concurrent submissions. Independent CLI refreshes
    # otherwise race while updating the same system-keyring token records.
    access_token = os.environ.get("HAWK_ACCESS_TOKEN") or await login.access_token()
    auth_lock = asyncio.Lock()
    rows: list[dict[str, Any]] = []
    if args.resume:
        rows = json.loads((output / "summary.json").read_text())

    async def client() -> HawkClient:
        async with auth_lock:
            token = os.environ.get("HAWK_ACCESS_TOKEN") or await login.access_token()
        return HawkClient(token=token, api_url=api)

    async def case(path: Path) -> None:
        name = path.name.removesuffix(".eval-set.yaml")
        existing = next((r for r in rows if r["case"] == name), None)
        if (
            existing is not None
            and args.retry_blocked
            and existing["status"] == "blocked"
            and not existing.get("job")
        ):
            existing["status"] = "preparing"
        if existing is not None and existing["status"] in (
            "passed",
            "partial",
            "failed",
            "blocked",
            "timed_out",
        ):
            return
        row = existing or {
            "case": name,
            "status": "preparing",
            "revision": revision,
            "viewer": args.viewer.rstrip("/"),
        }
        if existing is None:
            rows.append(row)
        directory = output / name
        directory.mkdir(parents=True, exist_ok=True)
        try:
            config = materialize(yaml.safe_load(path.read_text()), revision)
            config_path = directory / "config.yaml"
            config_path.write_text(yaml.safe_dump(config, sort_keys=False))
            if not row.get("job"):
                spec = config["tasks"][0]["items"][0]["args"]["spec"]
                environment = dict(os.environ)
                environment["HAWK_ACCESS_TOKEN"] = access_token
                secrets = spec.get("child_secrets", [])
                if "HF_TOKEN" in secrets and not environment.get("HF_TOKEN"):
                    from huggingface_hub import get_token

                    if token := get_token():
                        environment["HF_TOKEN"] = token
                missing = [s for s in secrets if not environment.get(s)]
                if missing:
                    row.update(
                        status="blocked", reason=f"Missing declared secrets: {', '.join(missing)}"
                    )
                    return
                command = [login.binary, "eval-set", str(config_path)]
                for secret in secrets:
                    command += ["--secret", secret]
                launched = await asyncio.to_thread(
                    subprocess.run,
                    command,
                    capture_output=True,
                    text=True,
                    env=environment,
                    timeout=180,
                    check=False,
                )
                if launched.returncode:
                    row.update(status="blocked", reason="Hawk submission failed")
                    (directory / "submission.txt").write_text(launched.stdout + launched.stderr)
                    return
                match = re.search(r"Eval set ID: (\S+)", launched.stdout)
                if match is None:
                    raise RuntimeError("Hawk returned no eval-set ID")
                row.update(job=match[1], status="submitted", submitted_at=time.time())
                row.pop("reason", None)
                print(f"{name}: {row['job']}", flush=True)
                write_results(output, rows)
            if args.submit_only:
                return
            deadline = row.get("submitted_at", time.time()) + args.timeout
            while time.time() < deadline:
                async with await client() as hawk:
                    status = await hawk.get_job_status(row["job"])
                if status and status.status in ("complete", "failed", "deleted", "stuck"):
                    break
                await asyncio.sleep(20)
            else:
                async with await client() as hawk:
                    await hawk.stop_eval_set(row["job"], error=True)
                row.update(status="timed_out", reason="Suite deadline reached; stop requested")
                return
            downloaded = await asyncio.to_thread(
                subprocess.run,
                [login.binary, "download", row["job"], "-o", str(directory / "logs"), "--force"],
                capture_output=True,
                text=True,
                timeout=180,
                check=False,
            )
            paths = list((directory / "logs").glob("*.eval"))
            if downloaded.returncode or not paths:
                row.update(
                    status="failed",
                    reason="No completed Inspect logs; inspect Hawk runner diagnostics",
                )
            else:
                row.update(await asyncio.to_thread(summarize, paths))
            if status and status.status == "stuck":
                async with await client() as hawk:
                    await hawk.stop_eval_set(row["job"], error=True)
        except Exception as ex:
            row.update(status="failed", reason=type(ex).__name__)
            # The native logs and Hawk retain detailed diagnostics; exceptions can
            # contain signed URLs or tokens, so they are not printed here.
        finally:
            write_results(output, rows)
            print(f"{name}: {row['status']}", flush=True)

    await asyncio.gather(*(case(path) for path in files))
    return 1 if any(r["status"] in ("failed", "blocked", "timed_out") for r in rows) else 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ref")
    parser.add_argument("--cases", help="Comma-separated case names; omitted runs all cases")
    parser.add_argument("--output", type=Path, default=ROOT / "out" / "compatibility")
    parser.add_argument("--timeout", type=int, default=1800)
    parser.add_argument("--submit-only", action="store_true")
    parser.add_argument("--prepare-only", action="store_true")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument(
        "--retry-blocked",
        action="store_true",
        help="With --resume, retry cases that failed before receiving a job ID",
    )
    parser.add_argument("--viewer", default="https://viewer.hawk.hawk.generalitylabs.ai")
    raise SystemExit(asyncio.run(run(parser.parse_args())))
