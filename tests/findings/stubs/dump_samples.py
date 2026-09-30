"""Stub sample dump: write an empty samples file and the meta a real dump would, ignore other arguments.

The last three arguments are the task spec, the samples path and the meta path. STUB_META (JSON)
replaces the default meta. STUB_RECORD, when set, names a file that receives how the stub was run:
argv, working directory, UV_PROJECT_ENVIRONMENT and PYTHONDONTWRITEBYTECODE. STUB_DUMP_EXIT sets
the exit code.
"""

import json
import os
import sys
from pathlib import Path

spec, samples, meta = sys.argv[-3:]
Path(samples).write_text("")
default = {
    "task": spec,
    "dataset_name": "McGill-NLP/stereoset",
    "dataset_location": "McGill-NLP/stereoset",
    "samples": 2123,
}
Path(meta).write_text(os.environ.get("STUB_META", json.dumps(default)))
if "STUB_RECORD" in os.environ:
    record = {
        "argv": sys.argv,
        "cwd": os.getcwd(),
        "env": os.environ.get("UV_PROJECT_ENVIRONMENT"),
        "bytecode": os.environ.get("PYTHONDONTWRITEBYTECODE"),
    }
    Path(os.environ["STUB_RECORD"]).write_text(json.dumps(record))
if os.environ.get("STUB_DUMP_EXIT", "0") != "0":
    sys.stderr.write("dump boom\n")
sys.exit(int(os.environ.get("STUB_DUMP_EXIT", "0")))
