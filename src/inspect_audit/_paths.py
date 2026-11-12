"""Working folders whose paths are reproducible."""

import shutil
import tempfile
from pathlib import Path


def stable_dir(kind: str, key: str) -> Path:
    """A fresh folder whose path depends only on what it holds.

    The paths of fetched logs reach the investigation's recorded plan, which Inspect
    hashes into the task's identity; a random temp path made every restarted runner a
    different task, which eval_set then refused to resume (FRICTION 42).
    """
    import hashlib

    path = (
        Path(tempfile.gettempdir())
        / f"inspect_audit_{kind}"
        / hashlib.sha256(key.encode()).hexdigest()[:16]
    )
    shutil.rmtree(path, ignore_errors=True)
    path.mkdir(parents=True)
    return path
