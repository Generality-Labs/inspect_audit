"""Where logs come from: one place resolves every way a log can be named.

A local `.eval`/`.json` file or folder, `hawk:<eval set id>[/<file>]` (declared
Hawk inputs first, else the warehouse), or an `s3://`, `gs://` or `http(s)://` URL.
Reading a log, once it is local, is `_logs`' business.
"""

import shutil
import tempfile
from pathlib import Path

from inspect_ai.log import list_eval_logs

from .dispatch.hawk import declared_transcripts, fetch_eval_sets


def is_remote_logs(source: str) -> bool:
    """Whether a log source is already parked somewhere a Hawk job can read it."""
    return source.startswith(("hawk:", "s3://", "gs://", "http://", "https://"))


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
        declared = declared_transcripts()
        if declared is not None:
            source_id = logs.removeprefix("hawk:")
            if source_id not in declared:
                raise ValueError(f"Hawk input {source_id!r} was not declared for this execution")
            return declared[source_id]
        return fetch_eval_sets(logs.removeprefix("hawk:"))
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
