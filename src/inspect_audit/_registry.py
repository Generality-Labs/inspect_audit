"""The package's Inspect tasks, `audit` and `investigate`, and log fetching."""

import asyncio
import importlib.util
import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any

from inspect_ai import Task, task
from inspect_ai.log import list_eval_logs

from ._investigate import investigate as investigate
from ._prices import register_prices
from ._task import audit_task


def resolve_source(source: str | None) -> str | None:
    """`source=` as a path: a file or directory, or a module name resolved by Python."""
    if source is None or Path(source).exists():
        return source
    spec = importlib.util.find_spec(source)
    if spec is None or spec.origin is None:
        raise ValueError(f"source={source!r} is neither a path nor an importable module")
    return spec.origin


def log_files(logs: list[str]) -> list[str]:
    """Each `logs` entry as files: a `.eval` path, or `hawk:<eval set id>` fetched whole."""
    files: list[str] = []
    for entry in logs:
        if entry.startswith("hawk:"):
            files += [i.name.removeprefix("file://") for i in list_eval_logs(fetch_logs(entry))]
        elif entry.endswith((".eval", ".json")):
            files.append(entry)
        else:
            raise ValueError(f"logs entry {entry!r} must be a .eval file or hawk:<eval set id>")
    return files


@task
def audit(
    task: str,
    samples: list[str] | str,
    checks: list[str],
    cost_limit: float | None = None,
    task_args: dict[str, Any] | None = None,
    logs: list[str] | None = None,
    judge: dict[str, str] | None = None,
    source: str | None = None,
    compose: str | None = None,
    assessment_ids: dict[str, list[str]] | None = None,
    redact: list[str] | None = None,
    confidential: bool = False,
    notes: str | None = None,
    auditor_image: str | None = None,
    message_limit: int | None = None,
    time_limit: int | None = None,
    working_limit: int | None = None,
    model_prices: dict[str, dict[str, float]] | None = None,
) -> Task:
    """Audit a benchmark task from inside its own samples (see `audit_task` in `_task`).

    Everything is declared: `samples` is a list of ids or the literal "all"; `logs` are
    `.eval` files or `hawk:<eval set id>`; `source` is the task's file, a directory, or
    a module name. The auditor is the eval's own `--model`; `model_prices` registers
    prices for a job whose models Inspect does not price.
    """
    if isinstance(samples, str) and samples != "all":
        raise ValueError('samples must be a list of sample ids or the literal "all"')
    if model_prices:
        register_prices(model_prices)
    return audit_task(
        task,
        samples=samples if samples == "all" else list(samples),
        checks=checks,
        task_args=task_args,
        logs=log_files(logs) if logs is not None else None,
        judge=judge,
        source=resolve_source(source),
        compose=compose,
        assessment_ids=assessment_ids,
        redact=redact or (),
        confidential=confidential,
        notes=notes,
        auditor_image=auditor_image,
        cost_limit=cost_limit,
        message_limit=message_limit,
        time_limit=time_limit,
        working_limit=working_limit,
    )


def fetch_logs(logs: str | list[str]) -> str:
    """Resolve a `logs` argument to something inspect can read locally.

    `hawk:<eval-set-id>[,<id>...]` downloads an eval set from the Hawk warehouse;
    anything else (a path, an `s3://` dir inspect reads natively) passes through.
    """
    if isinstance(logs, list):
        combined = Path(tempfile.mkdtemp(prefix="audit_corpus_"))
        for index, source in enumerate(logs):
            resolved = fetch_logs(source)
            files = (
                [resolved]
                if resolved.endswith(".eval")
                else [i.name for i in list_eval_logs(resolved)]
            )
            for filename in files:
                source_path = Path(filename.removeprefix("file://"))
                shutil.copyfile(source_path, combined / f"{index}_{source_path.name}")
        return str(combined)
    if logs.startswith("hawk:"):
        return _hawk_fetch(logs.removeprefix("hawk:"))
    return logs


def _hawk_fetch(eval_sets: str) -> str:
    """Download eval logs from the Hawk warehouse to a temporary directory.

    Talks to the Hawk API directly with the runner's own credentials -- the hawk
    CLI stores tokens in an OS keyring, which headless runner pods do not have.
    Not `_runs.HawkLogin`: task construction may run where no hook or login exists
    yet, and the HAWK_TOKEN_REFRESH_* variables are there either way.
    Requires HAWK_API_URL, plus either HAWK_ACCESS_TOKEN or the runner's token
    refresh environment (HAWK_TOKEN_REFRESH_URL, HAWK_TOKEN_REFRESH_CLIENT_ID,
    HAWK_REFRESH_TOKEN).
    """
    import concurrent.futures

    from hawk.cli.download import download_file
    from hawk.client import HawkClient

    fetched = Path(tempfile.mkdtemp(prefix="hawk_logs_"))
    sets = eval_sets.split(",")

    async def fetch() -> None:
        async with HawkClient(token=_hawk_token(), api_url=os.environ["HAWK_API_URL"]) as client:
            for address in sets:
                eval_set, _, selected_file = address.partition("/")
                files = await client.get_log_files(eval_set)
                names = [f["name"] for f in files if str(f.get("name", "")).endswith(".eval")]
                if selected_file:
                    names = [name for name in names if Path(name).name == selected_file]
                    if len(names) != 1:
                        raise ValueError(
                            f"Expected exactly one Hawk log at {address!r}, found {len(names)}"
                        )
                if not names:
                    raise ValueError(f"No .eval files found in Hawk eval set {eval_set!r}.")
                # several sets share one flat directory: prefix so same-named files
                # cannot silently overwrite each other
                prefix = f"{eval_set}_" if len(sets) > 1 else ""
                async for url, filename in client.get_download_urls(names):
                    await download_file(url, fetched / f"{prefix}{Path(filename).name}")

    # task construction may already be inside inspect's event loop
    with concurrent.futures.ThreadPoolExecutor(1) as pool:
        pool.submit(asyncio.run, fetch()).result()
    return str(fetched)


def _hawk_token() -> str:
    # a fresh token via the runner's refresh credentials, else the static one
    refresh_url = os.environ.get("HAWK_TOKEN_REFRESH_URL")
    refresh_token = os.environ.get("HAWK_REFRESH_TOKEN")
    client_id = os.environ.get("HAWK_TOKEN_REFRESH_CLIENT_ID")
    if refresh_url and refresh_token and client_id:
        import urllib.parse
        import urllib.request

        body = urllib.parse.urlencode(
            {
                "grant_type": "refresh_token",
                "client_id": client_id,
                "refresh_token": refresh_token,
            }
        ).encode()
        req = urllib.request.Request(
            refresh_url,
            data=body,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        with urllib.request.urlopen(req, timeout=60) as r:
            return str(json.load(r)["access_token"])
    token = os.environ.get("HAWK_ACCESS_TOKEN")
    if not token:
        raise ValueError(
            "Fetching hawk: logs needs HAWK_ACCESS_TOKEN or the runner's token refresh environment."
        )
    return token
