import inspect as inspect_module
import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any

from inspect_ai import Task, task, task_with
from inspect_ai.log import list_eval_logs
from inspect_ai.solver import Generate, Solver, TaskState, solver
from inspect_ai.util import sandbox

from ._agent import audit_items, effort_problem
from ._audit import audit_task
from ._concordance import Concordance
from ._investigate import investigate as investigate
from ._prices import register_prices
from ._resolve import resolve_task, resolve_task_from_log
from ._sandbox import (
    BENCHMARK_SERVICE,
    has_benchmark_box,
)


@task
def audit(
    task: str | None = None,
    task_args: dict[str, object] | None = None,
    logs: str | list[str] | None = None,
    samples: list[str] | None = None,
    limit: int | None = None,
    items: list[str] | None = None,
    model: str | None = None,
    reasoning_effort: str | None = None,
    notes: str | None = None,
    confidential: bool = False,
    redact: list[str] | None = None,
    attempts_task: str | None = None,
    auditor_image: str | None = None,
    benchmark_image: str | None = None,
    assessment_ids: dict[str, list[str]] | None = None,
    concordance_limit: int = 15,
    model_prices: dict[str, dict[str, float]] | None = None,
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
        notes: A free-form operator steer inserted into the auditor's system prompt.
        confidential: The benchmark is unpublished -- instruct the auditor not to
            transmit item content off the box.
        redact: Explicit metadata keys to strip from the item the auditor reads.
        attempts_task: Task whose attempts to join, when the logs record a sibling
            variant of the audited task (e.g. no-tools attempts at a tools task).
        auditor_image: Published auditor image; switches to Helm-values emission
            for k8s providers.
        benchmark_image: Published image for benchmark services that `build:`.
        assessment_ids: Optional mapping from sample IDs to assessment-unit IDs.
            Defaults to one unit per sample. Supply every selected sample; IDs
            must be nonempty and globally unique.
        concordance_limit: Maximum recorded attempts to regrade at setup.
        model_prices: Per-million prices by Inspect model name, set by the investigator
            at submission. Registered before Hawk applies the job's model_cost_config
            with set_model_cost, which refuses a model the runner's Inspect does not know.
    """
    if model_prices:
        register_prices(model_prices)
    elif os.environ.get("HAWK_JOB_ID"):
        # a job submitted without its price table: fetch OpenRouter's, best effort
        from ._investigate import register_openrouter_costs

        register_openrouter_costs()
    resolved_logs = fetch_logs(logs) if logs else None
    if task is None:
        if not resolved_logs:
            raise ValueError("Provide a task to audit, or logs recording one.")
        files = (
            [resolved_logs]
            if resolved_logs.endswith((".eval", ".json"))
            else [info.name for info in list_eval_logs(resolved_logs)]
        )
        if not files:
            raise ValueError(f"No logs found at {logs!r}.")
        target: str | Task = resolve_task_from_log(files[0])
    else:
        target = task

    return audit_task(
        target,
        resolved_logs,
        samples=samples,
        limit=limit,
        task_args=task_args,
        items=items,
        model=model,
        reasoning_effort=reasoning_effort,
        notes=notes,
        confidential=confidential,
        redact=redact,
        attempts_task=attempts_task,
        auditor_image=auditor_image,
        benchmark_image=benchmark_image,
        assessment_ids=assessment_ids,
        concordance_limit=concordance_limit,
    )


@task
def benchmark(
    task: str,
    task_args: dict[str, object] | None = None,
    model_prices: dict[str, dict[str, float]] | None = None,
) -> Task:
    """A benchmark task, run with the prices its job was submitted with.

    Hawk applies a job's model_cost_config with set_model_cost, which refuses a model
    the runner's Inspect has no entry for; a benchmark runs none of our code, so the
    investigator submits its tasks through this one. It registers the prices and
    returns the benchmark's own task under the benchmark's own name, so its log reads
    as the benchmark's (the registry entry records this wrapper and its arguments).

    Args:
        task: The benchmark task, e.g. `inspect_evals/simpleqa`.
        task_args: The benchmark task's own arguments.
        model_prices: Per-million prices by Inspect model name.
    """
    if model_prices:
        register_prices(model_prices)
    target = resolve_task(task, dict(task_args or {}))
    return task_with(target, name=target.name)


@solver
def audit_probe() -> Solver:
    """Assert the audit sandbox was assembled correctly, without spending on a model.

    Checks the auditor's filesystem and egress, that the sliced logs open with the
    installed inspect_ai, and that the benchmark service is populated and isolated.
    """

    async def solve(state: TaskState, generate: Generate) -> TaskState:
        checks: dict[str, str] = {}

        async def run(name: str, target: str | None, cmd: str) -> None:
            # `sandbox(name)` falls back to the default box when the sample has
            # only one, so a benchmark check on a box-less item would report the
            # auditor's own filesystem as the benchmark's
            if target is not None and not has_benchmark_box():
                checks[name] = "SKIP no benchmark box"
                return
            box = sandbox() if target is None else sandbox(target)
            try:
                r = await box.exec(["bash", "-c", cmd], timeout=120)
                checks[name] = (r.stdout or r.stderr).strip()[:200]
            except Exception as ex:
                checks[name] = f"EXCEPTION {type(ex).__name__}: {ex}"[:200]

        await run("auditor_fs", None, "ls /audit && ls /audit/logs | head -3")
        await run(
            "auditor_logs_open",
            None,
            'python -c "from inspect_ai.log import list_eval_logs, read_eval_log; '
            "ls=list_eval_logs('/audit/logs'); print(len(ls), read_eval_log(ls[0].name, header_only=True).eval.task)\"",
        )
        await run(
            "auditor_egress",
            None,
            "python -c \"import urllib.request; print(urllib.request.urlopen('https://example.com', timeout=10).status)\"",
        )
        await run(
            "benchmark_content", BENCHMARK_SERVICE, "pwd; ls / | head -8; ls 2>/dev/null | head -8"
        )
        await run("benchmark_isolated", BENCHMARK_SERVICE, "ls /audit 2>&1 | head -1")
        await run(
            "benchmark_egress",
            BENCHMARK_SERVICE,
            "timeout 10 python -c \"import urllib.request; print(urllib.request.urlopen('https://example.com', timeout=8).status)\" 2>&1 | tail -1 || echo BLOCKED",
        )

        # concordance: prove the resolution and the grade channel against the
        # logs -- replay recorded attempts and require our regrade to reproduce
        # their scores. writes a machine-readable artifact the orchestrator reads.
        await _probe_concordance(state, checks)

        state.store.set("probe", checks)
        state.output.completion = json.dumps(checks, indent=1)
        return state

    return solve


async def _probe_concordance(state: TaskState, checks: dict[str, str]) -> None:
    # the gate ran at setup; report what it stored
    con = state.store_as(Concordance)
    checks["concordance"] = con.verdict
    checks["concordance_reasons"] = ", ".join(con.reasons)[:200]


def fetch_logs(logs: str | list[str]) -> str:
    """Resolve a `logs` argument to something inspect can read locally.

    `hawk:<eval-set-id>[,<id>...]` downloads an eval set from the Hawk warehouse;
    anything else (a path, an `s3://` dir inspect reads natively) passes through.
    """
    if isinstance(logs, list):
        combined = Path(tempfile.mkdtemp(prefix="audit_corpus_"))
        for index, source in enumerate(logs):
            resolved = fetch_logs(source)
            files = (
                [resolved]
                if resolved.endswith(".eval")
                else [i.name for i in list_eval_logs(resolved)]
            )
            for filename in files:
                source_path = Path(filename.removeprefix("file://"))
                shutil.copyfile(source_path, combined / f"{index}_{source_path.name}")
        return str(combined)
    if logs.startswith("hawk:"):
        return _hawk_fetch(logs.removeprefix("hawk:"))
    return logs


def _hawk_fetch(eval_sets: str) -> str:
    """Download eval logs from the Hawk warehouse to a temporary directory.

    Talks to the Hawk API directly with the runner's own credentials -- the hawk
    CLI stores tokens in an OS keyring, which headless runner pods do not have.
    Not `_jobs.Hawk`: that takes its token from Hawk's model-key hook, which direct
    children switch off (HAWK_RUNNER_REFRESH_URL=''), and it is async while task
    construction is not. The HAWK_TOKEN_REFRESH_* variables survive either way.
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
    sets = eval_sets.split(",")
    for address in sets:
        eval_set, _, selected_file = address.partition("/")
        files = get_json(f"/view/logs/logs?log_dir={urllib.parse.quote(eval_set)}")["files"]
        names = [f["name"] for f in files if str(f.get("name", "")).endswith(".eval")]
        if selected_file:
            names = [name for name in names if Path(name).name == selected_file]
            if len(names) != 1:
                raise ValueError(
                    f"Expected exactly one Hawk log at {address!r}, found {len(names)}"
                )
        if not names:
            raise ValueError(f"No .eval files found in Hawk eval set {eval_set!r}.")
        # several sets share one flat directory: prefix so same-named files
        # cannot silently overwrite each other
        prefix = f"{eval_set}_" if len(sets) > 1 else ""
        urls = post_json("/view/logs/log-download-urls", {"logs": names})["urls"]
        for item in urls:
            dest = fetched / f"{prefix}{Path(item['filename']).name}"
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
            "Fetching hawk: logs needs HAWK_ACCESS_TOKEN or the runner's token refresh environment."
        )
    return token


def task_arg_problems(name: str, args: dict[str, Any]) -> list[str]:
    """What this package's task `name` would refuse in `args` before it touches anything.

    A child job installs for minutes before its task loads; the 09-28 Luna smoke
    child died there on reasoning_effort without model, and Hawk had accepted the
    config because the rule is ours. The checks that need the audited benchmark
    still run only in the runner.
    """
    tasks = {"audit": audit, "benchmark": benchmark, "investigate": investigate}
    if name not in tasks:
        return [f"inspect_audit has no task {name!r}; it has {', '.join(sorted(tasks))}"]
    try:
        inspect_module.signature(tasks[name]).bind(**args)
    except TypeError as ex:
        return [f"{name}: {ex}"]
    if name != "audit":
        return []
    problems: list[str] = []
    if problem := effort_problem(args.get("model"), args.get("reasoning_effort")):
        problems.append(
            f"audit: {problem}. On Hawk, set the auditor's effort on its model item "
            "instead: models[].items[].args.config.reasoning_effort"
        )
    try:
        audit_items(args.get("items"))
    except ValueError as ex:
        problems.append(f"audit: {ex}")
    return problems
