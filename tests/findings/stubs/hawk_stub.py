"""Stub `hawk` CLI: `download <set> -o <dir>` copies the .eval named by STUB_EVAL_SRC into <dir>.

Records every invocation's argv as one line in STUB_HAWK_LOG so tests can see what was asked.
"""

import os
import shutil
import sys
from pathlib import Path

if log := os.environ.get("STUB_HAWK_LOG"):
    with open(log, "a") as handle:
        handle.write(" ".join(sys.argv[1:]) + "\n")

if sys.argv[1:2] == ["download"]:
    out = Path(sys.argv[sys.argv.index("-o") + 1])
    out.mkdir(parents=True, exist_ok=True)
    src = Path(os.environ["STUB_EVAL_SRC"])
    dest = out / src.name
    if not dest.exists():  # the real CLI skips files already present
        shutil.copyfile(src, dest)
    print(f"Downloaded 1 files to {out}")
    sys.exit(int(os.environ.get("STUB_EXIT", "0")))
sys.stderr.write(f"stub hawk: unsupported {sys.argv[1:]}\n")
sys.exit(2)
