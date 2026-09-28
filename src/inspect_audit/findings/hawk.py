"""Logs that live on Hawk: find the eval sets that ran a task, and pull their files into a local cache.

Discovery goes through the Hawk Python client, which resolves the operator's `hawk login`
token from the keyring. Download shells out to the `hawk` CLI, which presigns and fetches
concurrently and skips files already on disk, so a warm cache costs one listing call.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .adapters import ProducerError, run_command
from .producers import ProducerConfig

DEFAULT_CACHE = Path.home() / ".cache" / "inspect_audit" / "hawk"
PAGE_SIZE = 500  # the server's maximum for /meta/eval-sets

FetchPage = Callable[[int, int], Awaitable[dict[str, Any]]]


@dataclass(frozen=True)
class EvalSetInfo:
    eval_set_id: str
    created_at: str
    created_by: str
    eval_count: int
    task_names: list[str]


def _tail(name: str) -> str:
    return name.rsplit("/", 1)[-1]


def matches_task(task_names: Sequence[str], task: str) -> bool:
    """Whether any recorded task name is `task`.

    Two qualified names must match exactly: `audit/inspect_evals/scicode` is the sample
    auditor run over scicode, not scicode. A bare name (a task run from a file, or a
    registry name recorded without its package) matches on the unqualified tail.
    """
    wanted = _tail(task)
    for name in task_names:
        if name == task:
            return True
        if ("/" not in name or "/" not in task) and _tail(name) == wanted:
            return True
    return False


async def _fetch_page_via_client(page: int, limit: int) -> dict[str, Any]:
    try:
        import hawk.client
    except ImportError as ex:  # pragma: no cover - install-time problem
        raise ProducerError("finding Hawk eval sets needs the hawk package: pip install 'inspect_audit[remote]'") from ex
    async with hawk.client.HawkClient() as client:
        # the public get_eval_sets() has no page argument and the server caps a page at 500,
        # so walk the endpoint directly; it is the same request the client makes
        data = await client._request_json(  # noqa: SLF001 - no public paging API
            "GET", "/meta/eval-sets", params=[("page", str(page)), ("limit", str(limit))]
        )
    if not isinstance(data, dict):
        raise ProducerError("Hawk returned no eval-set page")
    return data


def find_eval_sets(
    task: str, *, fetch_page: FetchPage = _fetch_page_via_client, page_size: int = PAGE_SIZE
) -> list[EvalSetInfo]:
    """Every eval set whose recorded task names include `task`, newest first."""

    async def walk() -> list[EvalSetInfo]:
        found: list[EvalSetInfo] = []
        page = 1
        while True:
            data = await fetch_page(page, page_size)
            items = data.get("items") or []
            for item in items:
                names = [str(n) for n in (item.get("task_names") or [])]
                if matches_task(names, task):
                    found.append(
                        EvalSetInfo(
                            eval_set_id=str(item["eval_set_id"]),
                            created_at=str(item.get("created_at") or ""),
                            created_by=str(item.get("created_by") or ""),
                            eval_count=int(item.get("eval_count") or 0),
                            task_names=names,
                        )
                    )
            if len(items) < page_size:
                return found
            page += 1

    found = asyncio.run(walk())
    return sorted(found, key=lambda s: s.created_at, reverse=True)


def download_eval_set(eval_set_id: str, cache_dir: Path, producers: ProducerConfig) -> list[Path]:
    """`hawk download <set> -o <cache>/<set>`, then the `.eval` files now in that directory."""
    target = cache_dir / eval_set_id
    target.mkdir(parents=True, exist_ok=True)
    argv = [*producers.hawk, "download", eval_set_id, "-o", str(target)]
    result = run_command(argv, timeout=producers.timeout_s)
    if result.returncode != 0:
        raise ProducerError(f"hawk download {eval_set_id} failed (exit {result.returncode}): {(result.stderr or result.stdout)[-1500:]}")
    return sorted(target.rglob("*.eval"))
