import json
import subprocess
import tempfile

from inspect_ai import Task, task
from inspect_ai.agent import as_solver
from inspect_ai.log import list_eval_logs
from inspect_ai.solver import Generate, Solver, TaskState, solver
from inspect_ai.util import sandbox

from ._agent import audit_agent
from ._audit import audit_task
from ._resolve import resolve_task_from_log
from ._sandbox import BENCHMARK_SERVICE


@task
def audit(
    task: str | None = None,
    task_args: dict[str, object] | None = None,
    logs: str | None = None,
    samples: list[str] | None = None,
    limit: int | None = None,
    items: list[str] | None = None,
    model: str | None = None,
    auditor_image: str | None = None,
    benchmark_image: str | None = None,
) -> Task:
    """Audit a benchmark task from its logs.

    Args:
        task: Task to audit (defaults to the task the logs record).
        task_args: Task arguments used to resolve the audited task.
        logs: Log file or directory of logs holding the recorded attempts.
        samples: Sample ids to audit (defaults to all, subject to `limit`).
        limit: Audit at most this many samples.
        items: Audit items to investigate (defaults to all of them).
        model: Model to audit with (defaults to the evaluated model).
        auditor_image: Published auditor image; switches to Helm-values emission
            for k8s providers.
        benchmark_image: Published image for benchmark services that `build:`.
    """
    # `hawk:<eval-set-id>[,<id>...]` fetches logs from the Hawk warehouse
    if logs and logs.startswith("hawk:"):
        fetched = tempfile.mkdtemp(prefix="hawk_logs_")
        for eval_set in logs.removeprefix("hawk:").split(","):
            subprocess.run(["hawk", "download", eval_set], cwd=fetched, check=True)
        logs = fetched
    if task is None:
        if not logs:
            raise ValueError("Provide a task to audit, or logs recording one.")
        files = (
            [logs]
            if logs.endswith((".eval", ".json"))
            else [info.name for info in list_eval_logs(logs)]
        )
        if not files:
            raise ValueError(f"No logs found at {logs!r}.")
        target: str | Task = resolve_task_from_log(files[0])
    else:
        target = task

    return audit_task(
        target,
        logs,
        samples=samples,
        limit=limit,
        task_args=task_args,
        items=items,
        solver=as_solver(audit_agent(items=items, model=model)) if model else None,
        auditor_image=auditor_image,
        benchmark_image=benchmark_image,
    )


@solver
def audit_probe() -> Solver:
    """Assert the audit sandbox was assembled correctly, without spending on a model.

    Checks the auditor's filesystem and egress, that the sliced logs open with the
    installed inspect_ai, and that the benchmark service is populated and isolated.
    """

    async def solve(state: TaskState, generate: Generate) -> TaskState:
        checks: dict[str, str] = {}

        async def run(name: str, target: str | None, cmd: str) -> None:
            box = sandbox() if target is None else sandbox(target)
            try:
                r = await box.exec(["bash", "-c", cmd], timeout=120)
                checks[name] = (r.stdout or r.stderr).strip()[:200]
            except Exception as ex:
                checks[name] = f"EXCEPTION {type(ex).__name__}: {ex}"[:200]

        await run("auditor_fs", None, "ls /audit && ls /audit/logs | head -3")
        await run(
            "auditor_logs_open", None,
            "python -c \"from inspect_ai.log import list_eval_logs, read_eval_log; "
            "ls=list_eval_logs('/audit/logs'); print(len(ls), read_eval_log(ls[0].name, header_only=True).eval.task)\"",
        )
        await run("auditor_egress", None,
                  "python -c \"import urllib.request; print(urllib.request.urlopen('https://example.com', timeout=10).status)\"")
        await run("benchmark_content", BENCHMARK_SERVICE, "pwd; ls / | head -8; ls 2>/dev/null | head -8")
        await run("benchmark_isolated", BENCHMARK_SERVICE, "ls /audit 2>&1 | head -1")
        await run("benchmark_egress", BENCHMARK_SERVICE,
                  "timeout 10 python -c \"import urllib.request; print(urllib.request.urlopen('https://example.com', timeout=8).status)\" 2>&1 | tail -1 || echo BLOCKED")

        state.store.set("probe", checks)
        state.output.completion = json.dumps(checks, indent=1)
        return state

    return solve
