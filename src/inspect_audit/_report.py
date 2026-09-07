"""Layer-2 synthesis: the top-level agent that sits above completed audits.

This is step 4 of the README pipeline ("Synthesize"). The current rung: the
agent works inside a sandbox with the audited run's logs staged at
/report/logs (and inspect-ai + pandas installed, so the log API works in
place), while the operator connects to its head over ACP. The frames layer
and the synthesis skill land on top of this.

Run it interactively:

    inspect eval inspect_audit/report -T logs=<log-dir> \
        --model <model> --acp-server --display none

then attach from another shell with `inspect acp`, or through the web chat
(`python frontend/server.py`; see frontend/README.md).

With no `logs` argument the task degrades to the sandbox-less chat skeleton
(useful for exercising the ACP plumbing without docker).
"""

import atexit
import shutil
import tempfile
from importlib.metadata import version
from pathlib import Path

from acp.schema import ElicitationSchema, ElicitationStringPropertySchema
from inspect_ai import Task
from inspect_ai.agent import AgentState, react
from inspect_ai.dataset import Sample
from inspect_ai.log import list_eval_logs
from inspect_ai.tool import bash, python, skill
from inspect_ai.util import request_input
from inspect_ai.util._sandbox.environment import SandboxEnvironmentType

from ._agent import SKILLS, SUPPORT_SKILLS
from ._sandbox import COMPOSE, DOCKERFILE

REPORT_ROOT = "/report"

# the synthesis skill lives apart from skills/ because dirs there are
# enumerated as audit items for auditors (see _agent.audit_skills)
REPORT_SKILLS = Path(__file__).parent / "report_skills"

REPORT_PROMPT = f"""You are the synthesis agent for a benchmark audit. You sit
above the logs of a completed run and work with a human operator to turn them
into findings.

Your sandbox has the run's logs at {REPORT_ROOT}/logs, with inspect-ai and
pandas installed.

Invoke the `synthesis` skill and follow it. The reading-logs and analyzing-logs
skills cover the log APIs (read headers and summaries before samples; never
unzip .eval files).

Work at the operator's direction. Ground every claim in something you actually
read from the logs, and say so when you haven't. Never call submit() until the
operator says the session is finished."""

CHAT_ONLY_PROMPT = """You are the synthesis agent for a benchmark audit,
running in plumbing-test mode: no logs were staged and you have no tools.
Converse with the operator; never call submit() until they say the session is
finished."""


async def _operator_turn(state: AgentState) -> bool | str:
    """Hand the floor to the operator whenever the model stops calling tools.

    react()'s default on_continue nudges the model to keep going, which makes
    a conversational agent chatter to itself. Instead we block on an ACP
    elicitation until the operator replies; their text becomes the next user
    message. Declining or cancelling the form ends the agent.
    """
    if state.output.message.tool_calls:
        return True
    result = await request_input(
        message="Reply to the agent",
        schema=ElicitationSchema(
            properties={
                "message": ElicitationStringPropertySchema(
                    type="string", title="Message"
                )
            },
            required=["message"],
        ),
    )
    if result.outcome == "accepted" and result.content:
        return str(result.content["message"])
    return False


def _staged_logs(logs: str) -> dict[str, str]:
    """Container path -> host path for every log under `logs`.

    Uses `list_eval_logs` (which knows what a log file is) and keeps each
    file's path relative to the log root, so same-named logs in different
    subdirectories cannot silently collide.
    """
    root = Path(logs).resolve()
    if root.is_file():
        return {f"{REPORT_ROOT}/logs/{root.name}": str(root)}
    files = [
        Path(info.name.removeprefix("file://")).resolve()
        for info in list_eval_logs(str(root))
    ]
    if not files:
        raise ValueError(f"No logs found at {logs!r}.")
    return {
        f"{REPORT_ROOT}/logs/{f.relative_to(root)}": str(f) for f in files
    }


def _report_sandbox() -> SandboxEnvironmentType:
    """The audit module's generic sandbox, with the log-reading stack installed."""
    stage = Path(tempfile.mkdtemp(prefix="inspect_report_sandbox_"))
    atexit.register(shutil.rmtree, stage, ignore_errors=True)
    requirements = f"inspect-ai=={version('inspect-ai')} pandas pyarrow"
    (stage / "Dockerfile").write_text(DOCKERFILE.format(requirements=requirements))
    compose = stage / "compose.yaml"
    compose.write_text(COMPOSE)
    return ("docker", str(compose))


def report_task(logs: str | None = None) -> Task:
    """Build the synthesis session as an Inspect `Task`.

    Args:
        logs: Log file or directory of logs to stage into the sandbox at
            /report/logs. None runs the sandbox-less chat skeleton.
    """
    if logs is None:
        return Task(
            dataset=[
                Sample(
                    input="Greet the operator in one short sentence and wait "
                    "for direction."
                )
            ],
            solver=react(
                name="report",
                description="Synthesis agent (plumbing-test mode).",
                prompt=CHAT_ONLY_PROMPT,
                on_continue=_operator_turn,
            ),
        )

    files = _staged_logs(logs)
    intro = (
        f"{len(files)} log file(s) are staged at {REPORT_ROOT}/logs. Greet the "
        "operator in one short sentence and wait for direction."
    )
    return Task(
        dataset=[Sample(input=intro, files=files)],
        solver=react(
            name="report",
            description="Synthesis agent over completed audit logs.",
            prompt=REPORT_PROMPT,
            tools=[
                bash(timeout=300),
                python(timeout=300),
                skill(
                    [str(d) for d in sorted(REPORT_SKILLS.iterdir()) if d.is_dir()]
                    + [str(SKILLS / name) for name in SUPPORT_SKILLS]
                ),
            ],
            on_continue=_operator_turn,
        ),
        sandbox=_report_sandbox(),
    )
