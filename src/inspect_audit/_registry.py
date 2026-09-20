import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any

from inspect_ai import Task, task
from inspect_ai.log import list_eval_logs
from inspect_ai.solver import Generate, Solver, TaskState, solver
from inspect_ai.util import sandbox

from ._agent import grade_benchmark, reset_benchmark
from ._audit import audit_task
from ._concordance import Concordance
from ._feedback import binary_feedback as binary_feedback
from ._investigate import investigate as investigate
from ._resolve import resolve_task, resolve_task_from_log
from ._sandbox import (
    BENCHMARK_SERVICE,
    has_benchmark_box,
)
from ._scicode_audit import scicode_replay as scicode_replay
from ._scicode_feedback import scicode_feedback as scicode_feedback


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
    concordance_limit: int = 15,
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
        redact: Further metadata keys to strip from the item the auditor reads.
        attempts_task: Task whose attempts to join, when the logs record a sibling
            variant of the audited task (e.g. no-tools attempts at a tools task).
        auditor_image: Published auditor image; switches to Helm-values emission
            for k8s providers.
        benchmark_image: Published image for benchmark services that `build:`.
        concordance_limit: Maximum recorded attempts to regrade at setup.
    """
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
        concordance_limit=concordance_limit,
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


async def _probe_grade(state: TaskState, checks: dict[str, str]) -> None:
    async def grade_value(grade: Any) -> str:
        scores = json.loads(await grade(answer=""))["scores"]
        one = scores[0] if isinstance(scores, list) else scores
        return str(one.get("value"))

    try:
        item = (state.metadata or {}).get("audit_item") or {}
        audited = item.get("task")
        if audited is None:
            checks["grade"] = "SKIP no audited task recorded"
            return
        resolved = resolve_task(audited, item.get("task_args") or {})
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

        await reset_benchmark()(hard=False)
        checks["grade_reset"] = await grade_value(grade)
    except Exception as ex:
        checks["grade"] = f"EXCEPTION {type(ex).__name__}: {ex}"[:200]


def fetch_logs(logs: str | list[str]) -> str:
    """Resolve a `logs` argument to something inspect can read locally.

    `hawk:<eval-set-id>[,<id>...]` downloads an eval set from the Hawk warehouse;
    anything else (a path, an `s3://` dir inspect reads natively) passes through.
    """
    if isinstance(logs, list):
        combined = Path(tempfile.mkdtemp(prefix="audit_corpus_"))
        for index, source in enumerate(logs):
            resolved = fetch_logs(source)
            files = [resolved] if resolved.endswith(".eval") else [i.name for i in list_eval_logs(resolved)]
            for filename in files:
                shutil.copyfile(filename, combined / f"{index}_{Path(filename).name}")
        return str(combined)
    if logs.startswith("hawk:"):
        return _hawk_fetch(logs.removeprefix("hawk:"))
    return logs


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
    sets = eval_sets.split(",")
    for address in sets:
        eval_set, _, selected_file = address.partition("/")
        files = get_json(f"/view/logs/logs?log_dir={urllib.parse.quote(eval_set)}")["files"]
        names = [f["name"] for f in files if str(f.get("name", "")).endswith(".eval")]
        if selected_file:
            names = [name for name in names if Path(name).name == selected_file]
            if len(names) != 1:
                raise ValueError(f"Expected exactly one Hawk log at {address!r}, found {len(names)}")
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
            "Fetching hawk: logs needs HAWK_ACCESS_TOKEN or the runner's token "
            "refresh environment."
        )
    return token
