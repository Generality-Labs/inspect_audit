"""A real box for tests: Inspect's own `local` sandbox, installed in Inspect's context.

Every `sandbox()` call then reaches it, as in a sample, with nothing patched per module.
Commands really run (on this machine, in a temporary folder), with this environment's
Python first on PATH, so the box can import the test benchmarks installed here.
"""

import os
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from inspect_ai.util._sandbox.context import (
    sandbox_default_context_var,
    sandbox_environments_context_var,
)
from inspect_ai.util._sandbox.local import LocalSandboxEnvironment


@contextmanager
def local_box() -> Iterator[LocalSandboxEnvironment]:
    box = LocalSandboxEnvironment()
    path = os.environ.get("PATH", "")
    os.environ["PATH"] = f"{Path(sys.executable).parent}{os.pathsep}{path}"
    environments = sandbox_environments_context_var.set({"default": box})
    default = sandbox_default_context_var.set("default")
    try:
        yield box
    finally:
        sandbox_default_context_var.reset(default)
        sandbox_environments_context_var.reset(environments)
        os.environ["PATH"] = path
        box.directory.cleanup()
