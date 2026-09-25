"""Free end-to-end check of the Hawk investigator's workspace path, in local Docker.

Builds the real `investigate(execution="hawk")` task, swaps only the k8s sandbox for a
Docker container of the same investigator image (no bind mounts, so every byte crosses
the same push/pull boundary as on Hawk) and the S3 artifact dir for a local directory,
then drives it with a scripted mock model that does what killed the 2026-09-23 chess
run: clone a repository that tracks a symlink, make a venv, write links, a FIFO and a
large file into /workspace, then call the synchronized host tools and publish.

Passes when the sample does not error and the durable state holds the journal.

    uv run python -u dev/e2e_hawk_workspace.py
"""

import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

import yaml
from inspect_ai import eval
from inspect_ai.model import ModelOutput, get_model
from inspect_ai.util import SandboxEnvironmentSpec

from inspect_audit._investigate import DEFAULT_INVESTIGATOR_IMAGE, investigate

MESSY_WORKSPACE = """set -e
cd /workspace
git clone -q https://github.com/Generality-Labs/inspect_audit.git inspect_audit
git -C inspect_audit checkout -q 8c2748bb7615c51f73783cd45f00e1cc32858e13
test -L inspect_audit/dev/logs
python -m venv env
ln -s /etc/passwd report/evil-link
echo x > report/plain.txt && ln report/plain.txt report/hard-link.txt
mkfifo report/fifo
head -c 150000000 /dev/urandom > big.bin
mkdir -p jobs && printf 'name: e2e\\n' > jobs/e2e.eval-set.yaml
echo '- e2e: made a messy workspace' >> journal.md
echo done
"""


def script() -> list[ModelOutput]:
    m = "mockllm/model"
    return [
        ModelOutput.for_tool_call(m, "bash", {"cmd": MESSY_WORKSPACE}),
        ModelOutput.for_tool_call(m, "jobs", {"action": "list", "label": None, "sample": None, "wait_minutes": None, "limit": None}),
        ModelOutput.for_tool_call(m, "hawk_submit", {"config": "/workspace/jobs/e2e.eval-set.yaml", "estimated_usd": 1.0, "note": None}),
        ModelOutput.for_tool_call(m, "hawk_submit", {"config": "/workspace/../etc/passwd", "estimated_usd": 1.0, "note": None}),
        ModelOutput.for_tool_call(m, "bash", {"cmd": "echo '- e2e: second entry' >> /workspace/journal.md"}),
        ModelOutput.for_tool_call(m, "publish_report", {}),
    ]


def main() -> int:
    scratch = Path(tempfile.mkdtemp(prefix="e2e-hawk-ws-"))
    artifacts = scratch / "artifacts"
    os.environ["HAWK_JOB_ID"] = "local-e2e"
    task = investigate(
        repo="https://github.com/Generality-Labs/epoch_bench.git",
        revision="60d75e5ea7afa17182352344577e3058cb62f27b",
        target_task="bench/Chess Puzzles",
        hawk_api_url="https://hawk.invalid",
        audit_package="git+https://github.com/Generality-Labs/inspect_audit.git@8c2748b",
        worker_models=["openai/gpt-5-mini"],
        artifact_dir="s3://placeholder/e2e/artifacts",
        output_dir=str(scratch / "runs"),
        enforce_cost_limit=False,
    )
    root = Path(task.metadata["investigation_dir"])
    (root / "remote-workspace.json").write_text(json.dumps({"artifact_dir": f"file://{artifacts}"}))
    compose = scratch / "compose.yaml"
    compose.write_text(yaml.safe_dump({"services": {"default": {
        "image": DEFAULT_INVESTIGATOR_IMAGE, "platform": "linux/amd64",
        "command": "sleep infinity", "init": True, "working_dir": "/workspace",
    }}}))
    task.sandbox = SandboxEnvironmentSpec("docker", str(compose))

    [log] = eval(
        task, model=get_model("mockllm/model", custom_outputs=script()),
        message_limit=20, log_dir=str(scratch / "logs"), display="plain",
    )
    sample = log.samples[0] if log.samples else None
    print("status:", log.status, "| sample error:", sample.error.message if sample and sample.error else None)
    for message in sample.messages if sample else []:
        if message.role == "tool":
            print(f"--- {message.function}: {str(message.text)[:600]}")
    saved = sorted(p.relative_to(artifacts).as_posix() for p in artifacts.rglob("*") if p.is_file())
    print("durable files:", len(saved))
    for path in saved:
        if "/state/" in path or "preflight" in path or "published" in path:
            print("  ", path)
    journal = [p for p in artifacts.rglob("journal.md")]
    ok = (
        sample is not None and sample.error is None
        and journal and "second entry" in journal[0].read_text()
    )
    print("PASS" if ok else "FAIL", "| scratch:", scratch)
    if ok:
        shutil.rmtree(scratch, ignore_errors=True)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
