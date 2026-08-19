import subprocess
import tempfile

from inspect_ai import Task, task
from inspect_ai.agent import as_solver
from inspect_ai.log import list_eval_logs

from ._agent import audit_agent
from ._audit import audit_task
from ._resolve import resolve_task_from_log


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
