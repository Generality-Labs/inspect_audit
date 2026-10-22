"""The package's Inspect tasks, `audit` and `investigate`, and log fetching."""

import asyncio
import importlib
import importlib.util
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any

from inspect_ai import Task, task
from inspect_ai.log import list_eval_logs
from inspect_ai.tool import WebSearchProviders

from ._investigate import investigate as investigate
from ._prices import register_prices
from ._publishing import publish as publish
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
    """Each `logs` entry as log files.

    A `.eval`/`.json` file is itself; `hawk:<eval set id>` is fetched whole; any
    other entry is a log directory, local or a URL Inspect reads (s3://, gs://),
    listed with Inspect's own `list_eval_logs`. An entry with no logs is an error.
    """
    files: list[str] = []
    for entry in logs:
        if entry.endswith((".eval", ".json")):
            files.append(entry)
            continue
        found = [
            i.name.removeprefix("file://")
            for i in list_eval_logs(fetch_logs(entry) if entry.startswith("hawk:") else entry)
        ]
        if not found:
            raise ValueError(f"logs entry {entry!r} holds no eval logs")
        files += found
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
    judge_configs: dict[str, dict[str, Any]] | None = None,
    source: str | None = None,
    compose: str | None = None,
    assessment_ids: dict[str, list[str]] | None = None,
    redact: list[str] | None = None,
    confidential: bool = False,
    notes: str | None = None,
    search: WebSearchProviders | None = None,
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
        judge_configs=judge_configs,
        source=resolve_source(source),
        compose=compose,
        assessment_ids=assessment_ids,
        redact=redact or (),
        confidential=confidential,
        notes=notes,
        search=search,
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
        declared = _declared_transcripts()
        if declared is not None:
            source_id = logs.removeprefix("hawk:")
            if source_id not in declared:
                raise ValueError(f"Hawk input {source_id!r} was not declared for this execution")
            return declared[source_id]
        return _hawk_fetch(logs.removeprefix("hawk:"))
    if logs.startswith(("s3://", "gs://", "http://", "https://")):
        return _download(logs)
    return logs


def _download(url: str) -> str:
    """A remote log file or folder, copied to a temporary directory.

    With fsspec, the filesystem layer Inspect reads these URLs through.
    """
    import fsspec  # pyright: ignore[reportMissingTypeStubs]

    fetched = Path(tempfile.mkdtemp(prefix="remote_logs_"))
    names = [url] if url.endswith((".eval", ".json")) else [i.name for i in list_eval_logs(url)]
    if not names:
        raise ValueError(f"No Inspect logs found: {url}")
    for name in names:
        with fsspec.open(name, "rb") as remote:  # pyright: ignore[reportUnknownMemberType]
            (fetched / Path(name).name).write_bytes(remote.read())  # pyright: ignore[reportUnknownMemberType,reportAttributeAccessIssue,reportUnknownArgumentType]
    return str(fetched / Path(url).name) if len(names) == 1 and names[0] == url else str(fetched)


def _declared_transcripts() -> dict[str, str] | None:
    """The eval sets Hawk resolved for this run (Hawk 4 `transcripts`), else None."""
    try:
        from hawk.runtime import transcript_sources  # pyright: ignore[reportMissingImports]
    except ImportError:
        if "HAWK_TRANSCRIPT_SOURCES" in os.environ:
            raise
        return None  # a Hawk without declared inputs
    if "HAWK_TRANSCRIPT_SOURCES" not in os.environ:
        return None
    return {s.eval_set_id: s.location for s in transcript_sources()}  # pyright: ignore[reportUnknownVariableType,reportUnknownMemberType]


def _hawk_fetch(eval_sets: str) -> str:
    """Download eval logs from the Hawk warehouse to a temporary directory.

    Hawk's API is HAWK_API_URL; the token is HAWK_ACCESS_TOKEN when set, else this
    process's own Hawk credentials (`_runs.HawkLogin`): the runner's hook, which a
    Hawk runner installs before it loads tasks, or the operator's `hawk` login.
    """
    import concurrent.futures

    from hawk.cli.download import download_file
    from hawk.client import HawkClient

    fetched = Path(tempfile.mkdtemp(prefix="hawk_logs_"))
    sets = eval_sets.split(",")

    api = os.environ.get("HAWK_API_URL")
    if not api:
        raise ValueError("fetching hawk: logs needs HAWK_API_URL, Hawk's API address")

    async def fetch() -> None:
        from ._runs import HawkLogin

        token = os.environ.get("HAWK_ACCESS_TOKEN") or await HawkLogin(api).access_token()
        async with HawkClient(token=token, api_url=api) as client:
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
