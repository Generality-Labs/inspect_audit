"""Running async code from sync code that may itself be inside an event loop.

Task construction is sync, but Inspect may already be running an event loop when it
builds a task, and `asyncio.run` refuses to start a second one in the same thread. The
one way this package bridges that: run the coroutine in a fresh thread with its own loop.
"""

import asyncio
import concurrent.futures
from collections.abc import Coroutine
from typing import Any, TypeVar

T = TypeVar("T")


def run(coroutine: Coroutine[Any, Any, T]) -> T:
    """The coroutine's result, whether or not this thread is inside an event loop."""
    with concurrent.futures.ThreadPoolExecutor(1) as pool:
        return pool.submit(asyncio.run, coroutine).result()
