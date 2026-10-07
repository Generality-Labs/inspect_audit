"""Stub producer: print the file named by STUB_OUTPUT_FILE to stdout, ignore arguments.

For `-o <dir>` style producers, when STUB_OUTPUT_DIR is set, copy that directory's
contents into the directory following `-o` instead. STUB_SCAN_RECORD, when set, names a file that
receives argv, the working directory, INSPECT_AUDIT_SAMPLES_FILE and INSPECT_AUDIT_SCORERS.
"""

import json
import os
import shutil
import sys
from pathlib import Path

if "STUB_OUTPUT_DIR" in os.environ and "-o" in sys.argv:
    target = Path(sys.argv[sys.argv.index("-o") + 1])
    shutil.copytree(os.environ["STUB_OUTPUT_DIR"], target, dirs_exist_ok=True)
else:
    sys.stdout.write(Path(os.environ["STUB_OUTPUT_FILE"]).read_text())
if "STUB_SCAN_RECORD" in os.environ:
    record = {
        "argv": sys.argv,
        "cwd": os.getcwd(),
        "samples": os.environ.get("INSPECT_AUDIT_SAMPLES_FILE"),
        "scorers": os.environ.get("INSPECT_AUDIT_SCORERS"),
    }
    Path(os.environ["STUB_SCAN_RECORD"]).write_text(json.dumps(record))
sys.exit(int(os.environ.get("STUB_EXIT", "0")))
