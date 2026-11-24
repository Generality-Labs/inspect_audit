"""Where logs come from: one place resolves every way a log can be named.

A local `.eval`/`.json` file or folder, `hawk:<eval set id>[/<file>]` (declared
Hawk inputs first, else the warehouse), or an `s3://`, `gs://` or `http(s)://` URL.
A `hawk:` log is read where it lives, by range, as `hawksrc://evals/<eval set id>/<file>`
(Hawk's own fsspec filesystem, which Inspect reads like `s3://`): a job reads its
header, its summaries and the entries it needs, never the whole file. `fetch_logs`
still downloads whole logs, for an auditor whose log access is `download`.
Reading a log is `_logs`' business. A saved findings bundle is
named the same ways (`fetch_bundle`): a local folder, a Hawk sample's artifact folder,
or a folder URL.
"""

from pathlib import Path

from inspect_ai.log import list_eval_logs

from ._paths import stable_dir
from .dispatch.hawk import declared_transcripts, fetch_artifacts, fetch_eval_sets


def is_remote_logs(source: str) -> bool:
    """Whether a log source is already parked somewhere a Hawk job can read it."""
    return source.startswith(("hawk:", "s3://", "gs://", "http://", "https://"))


def log_files(logs: list[str]) -> list[str]:
    """Each `logs` entry as log files, read where they live.

    A `.eval`/`.json` file is itself; `hawk:<eval set id>[/<file>]` is its declared
    location or its `hawksrc://` address (`hawk_logs`), never a download; any other
    entry is a log directory, local or a URL Inspect reads (s3://, gs://), listed with
    Inspect's own `list_eval_logs`. An entry with no logs is an error.
    """
    files: list[str] = []
    for entry in logs:
        if entry.startswith("hawk:"):
            files += hawk_logs(entry)
            continue
        if entry.endswith((".eval", ".json")):
            files.append(entry)
            continue
        found = [i.name.removeprefix("file://") for i in list_eval_logs(entry)]
        if not found:
            raise ValueError(f"logs entry {entry!r} holds no eval logs")
        files += found
    return files


def remote_log_files(source: str) -> list[str]:
    """A remote source's log files, as addresses Inspect reads by range, never downloaded."""
    if source.startswith("hawk:"):
        return hawk_logs(source)
    if source.endswith((".eval", ".json")):
        return [source]
    found = [i.name for i in list_eval_logs(source)]
    if not found:
        raise ValueError(f"No Inspect logs found: {source}")
    return found


def hawk_logs(source: str) -> list[str]:
    """The logs of `hawk:<eval set id>[,<id>...][/<file>]`, as addresses Inspect reads by range.

    An eval set Hawk resolved for this run (declared inputs) is read at its declared
    location; any other is `hawksrc://evals/<id>/<file>`, read through Hawk's API with
    this process's own token (a Hawk runner's, or the operator's login). An eval set
    with no logs is an error.
    """
    import fsspec  # pyright: ignore[reportMissingTypeStubs]

    declared = declared_transcripts() or {}
    files: list[str] = []
    for address in source.removeprefix("hawk:").split(","):
        eval_set, _, selected = address.strip("/").partition("/")
        if eval_set in declared:
            root = declared[eval_set]
        else:
            try:
                fsspec.get_filesystem_class("hawksrc")  # pyright: ignore[reportUnknownMemberType]
            except ValueError:
                raise ValueError(
                    f"hawk:{address}: reading Hawk logs in place needs the `hawk` package "
                    "with its hawksrc:// filesystem (hawk 4.2 or later) installed here"
                ) from None
            root = f"hawksrc://evals/{eval_set}"
        found = [i.name.removeprefix("file://") for i in list_eval_logs(root)]
        if selected:
            found = [f for f in found if f.rstrip("/").rsplit("/", 1)[-1] == selected]
            if len(found) != 1:
                raise ValueError(
                    f"Expected exactly one Hawk log at {address!r}, found {len(found)}"
                )
        if not found:
            raise ValueError(f"No eval logs found in Hawk eval set {address!r}")
        files += found
    return files


# Remote logs already fetched in this process, by address. An eval set builds one task
# per job item, and a hundred items naming the same Hawk log must not download it a
# hundred times into one runner.
_FETCHED: dict[str, str] = {}


def fetch_logs(logs: str) -> str:
    """Resolve one log source to something inspect can read locally.

    `hawk:<eval-set-id>[,<id>...]` downloads an eval set from the Hawk warehouse;
    anything else (a path, an `s3://` dir inspect reads natively) passes through.
    """
    if logs.startswith("hawk:"):
        # Hawk 4 resolves declared inputs in place; anything else is downloaded with this
        # runner's own user token. Declaring inputs requires restricted credentials, and a
        # restricted runner cannot launch jobs, so an audit reads its logs this way
        # (FRICTION 83); a restricted child without a declaration fails at the download.
        declared = declared_transcripts() or {}
        source_id = logs.removeprefix("hawk:")
        if source_id in declared:
            return declared[source_id]
    if not logs.startswith(("hawk:", "s3://", "gs://", "http://", "https://")):
        return logs
    cached = _FETCHED.get(logs)
    if cached is None or not Path(cached).exists():
        cached = (
            fetch_eval_sets(logs.removeprefix("hawk:"))
            if logs.startswith("hawk:")
            else _download(logs)
        )
        _FETCHED[logs] = cached
    return cached


def _download(url: str) -> str:
    """A remote log file or folder, copied to a temporary directory.

    With fsspec, the filesystem layer Inspect reads these URLs through.
    """
    import fsspec  # pyright: ignore[reportMissingTypeStubs]

    fetched = stable_dir("remote", url)
    names = [url] if url.endswith((".eval", ".json")) else [i.name for i in list_eval_logs(url)]
    if not names:
        raise ValueError(f"No Inspect logs found: {url}")
    for name in names:
        with fsspec.open(name, "rb") as remote:  # pyright: ignore[reportUnknownMemberType]
            (fetched / Path(name).name).write_bytes(remote.read())  # pyright: ignore[reportUnknownMemberType,reportAttributeAccessIssue,reportUnknownArgumentType]
    return str(fetched / Path(url).name) if len(names) == 1 and names[0] == url else str(fetched)


def fetch_bundle(source: str) -> str:
    """A saved findings folder, named locally or remotely, as a local folder.

    `hawk:<eval set id>/artifacts/<sample uuid>/<folder>` downloads that folder of a
    Hawk sample's artifacts through Hawk's API with this process's own token (what
    `hawk fs ls evals/<eval set id>/artifacts/...` lists); an `s3://` or `gs://` (any
    fsspec) folder is copied with fsspec, which needs this process's own read access;
    a local path passes through. Which folder it is (a bundle or a published folder
    holding one) is the reader's business.
    """
    if source.startswith("hawk:"):
        address = source.removeprefix("hawk:").strip("/")
        cached = _FETCHED.get(source)
        if cached is None or not Path(cached).exists():
            cached = _FETCHED[source] = fetch_artifacts(address)
        return cached
    if source.startswith(("http://", "https://")):
        raise ValueError(
            f"{source}: a findings bundle is a folder; name it as a local path, "
            "hawk:<eval set id>/artifacts/<sample uuid>/<folder>, or an s3:// or gs:// URL"
        )
    if "://" in source:
        cached = _FETCHED.get(source)
        if cached is None or not Path(cached).exists():
            import fsspec  # pyright: ignore[reportMissingTypeStubs]

            folder = source.rstrip("/")
            fetched = stable_dir("remote-bundle", folder)
            fs, path = fsspec.core.url_to_fs(folder)  # pyright: ignore[reportUnknownMemberType,reportUnknownVariableType]
            names = [str(n) for n in fs.find(path)]  # pyright: ignore[reportUnknownMemberType,reportUnknownVariableType,reportUnknownArgumentType]
            if not names:
                raise ValueError(f"No files found at {source}")
            for name in names:
                relative = Path(name).relative_to(path)
                if ".." in relative.parts:
                    raise ValueError(f"{source}: unsafe path {name!r}")
                (fetched / relative).parent.mkdir(parents=True, exist_ok=True)
                fs.get_file(name, str(fetched / relative))  # pyright: ignore[reportUnknownMemberType]
            cached = _FETCHED[source] = str(fetched)
        return cached
    return source
