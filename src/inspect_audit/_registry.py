import json
import os
import tempfile
from pathlib import Path
from typing import Any

from inspect_ai import Task, task
from inspect_ai.agent import as_solver
from inspect_ai.log import list_eval_logs
from inspect_ai.solver import Generate, Solver, TaskState, solver
from inspect_ai.util import sandbox

from ._agent import audit_agent, grade_benchmark, reset_benchmark
from ._audit import audit_task
from ._resolve import resolve_task, resolve_task_from_log
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
    reasoning_effort: str | None = None,
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
        reasoning_effort: Reasoning effort for the auditor model, when it takes one.
        auditor_image: Published auditor image; switches to Helm-values emission
            for k8s providers.
        benchmark_image: Published image for benchmark services that `build:`.
    """
    # `hawk:<eval-set-id>[,<id>...]` fetches logs from the Hawk warehouse
    if logs and logs.startswith("hawk:"):
        logs = _hawk_fetch(logs.removeprefix("hawk:"))
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
        solver=as_solver(audit_agent(items=items, model=model, reasoning_effort=reasoning_effort))
        if model
        else None,
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

        # grade/reset: grade the box pristine (expect fail), apply the sample's own
        # gold patch and grade again (expect pass), then reset and grade once more
        # (expect fail). proves the benchmark's grader runs against the box, that a
        # real solution is credited, and that reset returns the box to pristine.
        await _probe_grade(state, checks)

        state.store.set("probe", checks)
        state.output.completion = json.dumps(checks, indent=1)
        return state

    return solve


async def _probe_grade(state: TaskState, checks: dict[str, str]) -> None:
    async def grade_value(grade: Any) -> str:
        result = json.loads(await grade())
        one = result[0] if isinstance(result, list) else result
        return str(one.get("value"))

    try:
        item = (state.metadata or {}).get("audit_item") or {}
        resolved = resolve_task(item.get("task"), item.get("task_args") or {})
        scorers = resolved.scorer if isinstance(resolved.scorer, list) else [resolved.scorer]
        scorers = [s for s in scorers if s is not None]
        if not scorers:
            checks["grade"] = "SKIP no benchmark scorer"
            return
        grade = grade_benchmark(scorers)

        checks["grade_pristine"] = await grade_value(grade)

        # inject the sample's gold solution and grade again -- git-patch benchmarks
        # only; other shapes just exercise the pristine grade above
        benchmark_md = (state.metadata or {}).get("benchmark_metadata") or {}
        patch = benchmark_md.get("patch")
        if not patch:
            checks["grade"] = "SKIP gold injection is git-patch only"
            return
        applied = await sandbox(BENCHMARK_SERVICE).exec(
            ["bash", "-c", "cd /testbed && git apply -"], input=patch
        )
        checks["gold_applied"] = "ok" if applied.success else f"FAILED {applied.stderr[:120]}"
        checks["grade_gold"] = await grade_value(grade)

        await reset_benchmark()()
        checks["grade_reset"] = await grade_value(grade)
    except Exception as ex:
        checks["grade"] = f"EXCEPTION {type(ex).__name__}: {ex}"[:200]


def _hawk_fetch(eval_sets: str) -> str:
    """Download eval logs from the Hawk warehouse to a temporary directory.

    Talks to the Hawk API directly with the runner's own credentials -- the hawk
    CLI stores tokens in an OS keyring, which headless runner pods do not have.
    Requires HAWK_API_URL, plus either HAWK_ACCESS_TOKEN or the runner's token
    refresh environment (HAWK_TOKEN_REFRESH_URL, HAWK_TOKEN_REFRESH_CLIENT_ID,
    HAWK_REFRESH_TOKEN).
    """
    import urllib.parse
    import urllib.request

    api = os.environ["HAWK_API_URL"].rstrip("/")
    headers = {"Authorization": f"Bearer {_hawk_token()}"}

    def get_json(path: str) -> dict[str, Any]:
        req = urllib.request.Request(api + path, headers=headers)
        with urllib.request.urlopen(req, timeout=180) as r:
            return dict(json.load(r))

    def post_json(path: str, body: dict[str, Any]) -> dict[str, Any]:
        req = urllib.request.Request(
            api + path,
            data=json.dumps(body).encode(),
            headers={**headers, "Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=180) as r:
            return dict(json.load(r))

    fetched = Path(tempfile.mkdtemp(prefix="hawk_logs_"))
    for eval_set in eval_sets.split(","):
        files = get_json(f"/view/logs/logs?log_dir={urllib.parse.quote(eval_set)}")["files"]
        names = [f["name"] for f in files if str(f.get("name", "")).endswith(".eval")]
        if not names:
            raise ValueError(f"No .eval files found in Hawk eval set {eval_set!r}.")
        urls = post_json("/view/logs/log-download-urls", {"logs": names})["urls"]
        for item in urls:
            dest = fetched / Path(item["filename"]).name
            with urllib.request.urlopen(item["url"], timeout=600) as r, open(dest, "wb") as f:
                while chunk := r.read(1 << 20):
                    f.write(chunk)
    return str(fetched)


def _hawk_token() -> str:
    # a fresh token via the runner's refresh credentials, else the static one
    refresh_url = os.environ.get("HAWK_TOKEN_REFRESH_URL")
    refresh_token = os.environ.get("HAWK_REFRESH_TOKEN")
    client_id = os.environ.get("HAWK_TOKEN_REFRESH_CLIENT_ID")
    if refresh_url and refresh_token and client_id:
        import urllib.parse
        import urllib.request

        body = urllib.parse.urlencode(
            {
                "grant_type": "refresh_token",
                "client_id": client_id,
                "refresh_token": refresh_token,
            }
        ).encode()
        req = urllib.request.Request(
            refresh_url,
            data=body,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        with urllib.request.urlopen(req, timeout=60) as r:
            return str(json.load(r)["access_token"])
    token = os.environ.get("HAWK_ACCESS_TOKEN")
    if not token:
        raise ValueError(
            "Fetching hawk: logs needs HAWK_ACCESS_TOKEN or the runner's token "
            "refresh environment."
        )
    return token
