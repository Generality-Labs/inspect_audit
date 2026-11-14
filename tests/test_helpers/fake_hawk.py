"""A Hawk API on localhost, for testing our Hawk code through Hawk's own client.

Only the endpoints `dispatch.hawk` calls, answered with Hawk's own response models
(`hawk.core.types.monitoring`), so a field Hawk's client starts to require fails here
as it would against the real service. A job is driven by the test (`progress`,
`finish`, `stuck`, `forget`), and its logs are real `.eval` files served over HTTP
like presigned URLs. It also plays the identity provider a runner's refresh hook
calls (`runner_hook`), so Hawk's own hook runs unchanged.

    async with FakeHawk() as hawk:
        runs = HawkRuns(hawk.url, credential_mode="restricted")  # HAWK_ACCESS_TOKEN set
        run_id = await runs.submit(job)
        hawk.finish(run_id, logs=[path])
"""

import itertools
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from aiohttp import web
from aiohttp.test_utils import TestServer

# one sample as Hawk reports it: (finished?, model -> ModelUsage fields; None: none yet)
SampleUsage = tuple[bool, dict[str, dict[str, Any]] | None]


@dataclass
class FakeJob:
    config: dict[str, Any]
    body: dict[str, Any]
    status: str = "running"
    logs: list[Path] = field(default_factory=list)
    samples: list[SampleUsage] = field(default_factory=list)
    runner_log: list[str] = field(default_factory=list)
    stopped: bool = False


class FakeHawk:
    def __init__(
        self, prices: list[dict[str, Any]] | None = None, served: list[Any] | None = None
    ) -> None:
        self.jobs: dict[str, FakeJob] = {}
        self.prices = prices or []
        # what the proxy serves to this caller: public names, or {"name", "dead"} rows
        self.served: list[str | dict[str, Any]] = list(served or [])
        # a refusal Hawk answers every submission with (as when its proxy lacks a model)
        self.refuse: str | None = None
        # Hawk unreachable for status and monitoring calls
        self.down = False
        self.tokens_seen: list[str] = []
        # the identity provider's side: refresh tokens presented, and whether it rotates them
        self.grants: list[str] = []
        self.rotate = True
        self._ids = itertools.count(1)
        self._server: TestServer | None = None

    # --- the test's controls -------------------------------------------------

    def progress(self, run_id: str, samples: list[SampleUsage]) -> None:
        self.jobs[run_id].samples = list(samples)

    def finish(
        self,
        run_id: str,
        logs: list[Path] | None = None,
        samples: list[SampleUsage] | None = None,
        status: str = "complete",
    ) -> None:
        job = self.jobs[run_id]
        job.status, job.logs = status, list(logs or [])
        if samples is not None:
            job.samples = list(samples)

    def stuck(self, run_id: str) -> None:
        self.jobs[run_id].status = "stuck"

    def forget(self, run_id: str) -> None:
        """Hawk answers 404 for it from now on."""
        del self.jobs[run_id]

    @property
    def url(self) -> str:
        assert self._server is not None, "use `async with FakeHawk()`"
        return str(self._server.make_url("")).rstrip("/")

    # --- the API -------------------------------------------------------------

    def _seen(self, request: web.Request) -> None:
        self.tokens_seen.append(request.headers.get("Authorization", ""))

    def _job(self, request: web.Request) -> FakeJob:
        self._seen(request)
        if self.down:
            raise web.HTTPServiceUnavailable(text="Hawk API unreachable")
        job = self.jobs.get(request.match_info["id"])
        if job is None:
            raise web.HTTPNotFound(text=json.dumps({"detail": "not found"}))
        return job

    async def _auth_config(self, request: web.Request) -> web.Response:
        base = self.url
        return web.json_response(
            {
                "audience": "fake",
                "client_id": "fake",
                "issuer": base,
                "scopes": "openid",
                "scopes_supported": ["openid"],
                "authorization_endpoint": f"{base}/authorize",
                "device_authorization_endpoint": f"{base}/device",
                "jwks_uri": f"{base}/jwks",
                "token_endpoint": f"{base}/token",
                "middleman_url": f"{base}/middleman",
            }
        )

    async def _create(self, request: web.Request) -> web.Response:
        self._seen(request)
        if self.refuse:
            raise web.HTTPBadRequest(text=json.dumps({"detail": self.refuse}))
        body = await request.json()
        run_id = body.get("eval_set_id") or f"fake-{next(self._ids)}"
        self.jobs[run_id] = FakeJob(config=body.get("eval_set_config") or {}, body=body)
        return web.json_response({"eval_set_id": run_id, "warnings": []})

    async def _job_status(self, request: web.Request) -> web.Response:
        from hawk.core.types import monitoring

        job = self._job(request)
        reply = monitoring.JobStatusResponse(
            job_type="eval_set",
            status=job.status,  # pyright: ignore[reportArgumentType]
            pod_status=None,
            details=monitoring.EvalSetDetails(import_summary=None, warehouse_sample_count=0),
        )
        return web.json_response(json.loads(reply.model_dump_json()))

    async def _eval_set_status(self, request: web.Request) -> web.Response:
        from hawk.core.types import monitoring

        job = self._job(request)
        samples = [
            monitoring.SampleStatus(
                id=str(n + 1),
                epoch=1,
                phase="completed" if finished else "running",  # pyright: ignore[reportArgumentType]
                completed=finished,
                model_usage=None
                if usage is None
                else {
                    model: monitoring.SampleModelUsage.model_validate(counts)
                    for model, counts in usage.items()
                },
            )
            for n, (finished, usage) in enumerate(job.samples)
        ]
        reply = monitoring.EvalSetStatusResponse(
            etag="0",
            data=monitoring.EvalSetStatus(
                job_id=request.match_info["id"],
                status=job.status,  # pyright: ignore[reportArgumentType]
                progress=monitoring.StatusProgress(),
                tasks=[
                    monitoring.TaskStatus(
                        task_name="task",
                        log_file="task.eval",
                        progress=monitoring.StatusProgress(),
                        samples=samples,
                    )
                ],
            ),
        )
        return web.json_response(json.loads(reply.model_dump_json()))

    async def _runner_log(self, request: web.Request) -> web.Response:
        from hawk.core.types import monitoring

        job = self._job(request)
        entries = [
            monitoring.LogEntry(timestamp=datetime.now(UTC), service="runner", message=m)
            for m in job.runner_log
        ]
        reply = monitoring.LogsResponse(entries=entries)
        return web.json_response(json.loads(reply.model_dump_json()))

    async def _stop(self, request: web.Request) -> web.Response:
        job = self._job(request)
        job.stopped = True
        job.status = "complete" if job.logs else "failed"
        return web.json_response({})

    async def _log_files(self, request: web.Request) -> web.Response:
        self._seen(request)
        run_id = request.query["log_dir"]
        job = self.jobs.get(run_id)
        files = [{"name": f"{run_id}/{p.name}"} for p in (job.logs if job else [])]
        return web.json_response({"files": files})

    async def _download_urls(self, request: web.Request) -> web.Response:
        self._seen(request)
        body = await request.json()
        urls = [
            {"url": f"{self.url}/files/{name}", "filename": Path(name).name}
            for name in body["logs"]
        ]
        return web.json_response({"urls": urls})

    async def _file(self, request: web.Request) -> web.StreamResponse:
        run_id, name = request.match_info["id"], request.match_info["name"]
        for path in self.jobs[run_id].logs:
            if path.name == name:
                return web.FileResponse(path)
        raise web.HTTPNotFound()

    async def _prices(self, request: web.Request) -> web.Response:
        return web.json_response(self.prices)

    async def _permitted(self, request: web.Request) -> web.Response:
        self._seen(request)
        # like middleman: every model this caller may use, by its public name
        body = await request.json()
        bearer = request.headers.get("Authorization", "").removeprefix("Bearer ")
        assert body.get("api_key") == bearer
        return web.json_response([n if isinstance(n, dict) else {"name": n} for n in self.served])

    async def _token(self, request: web.Request) -> web.Response:
        """The identity provider's refresh-token grant, as a runner's hook calls it."""
        form = await request.post()
        assert form["grant_type"] == "refresh_token"
        self.grants.append(str(form["refresh_token"]))
        n = len(self.grants)
        reply: dict[str, Any] = {"access_token": f"access-{n}", "expires_in": 3600}
        if self.rotate:
            reply["refresh_token"] = f"refresh-{n}"
        return web.json_response(reply)

    async def _moved(self, request: web.Request) -> web.Response:
        raise web.HTTPFound(f"{self.url}/elsewhere")

    # --- lifecycle -----------------------------------------------------------

    def _app(self) -> web.Application:
        app = web.Application()
        app.router.add_get("/auth/config", self._auth_config)
        app.router.add_post("/token", self._token)
        app.router.add_post("/eval_sets/", self._create)
        app.router.add_post("/eval_sets/{id}/stop", self._stop)
        app.router.add_get("/monitoring/jobs/{id}/job-status", self._job_status)
        app.router.add_get("/monitoring/jobs/{id}/eval-set-status", self._eval_set_status)
        app.router.add_get("/monitoring/jobs/{id}/logs", self._runner_log)
        app.router.add_get("/view/logs/logs", self._log_files)
        app.router.add_post("/view/logs/log-download-urls", self._download_urls)
        app.router.add_get("/files/{id}/{name}", self._file)
        app.router.add_get("/usage/prices", self._prices)
        app.router.add_post("/middleman/permitted_models_info", self._permitted)
        # a proxy path that redirects: credentials must not follow it
        app.router.add_put("/middleman/moved", self._moved)
        return app

    async def __aenter__(self) -> "FakeHawk":
        self._server = TestServer(self._app())
        await self._server.start_server()
        return self

    async def __aexit__(self, *exc: object) -> None:
        assert self._server is not None
        await self._server.close()


def runner_hook(monkeypatch: Any, hawk: FakeHawk, tmp_path: Path) -> Any:
    """Hawk's own RefreshTokenHook, as a runner installs it, refreshing at `hawk`'s IdP.

    Nothing of Hawk's is replaced: the hook reads the environment a runner has and makes
    its real refresh-token grant over HTTP. Its token cache goes under `tmp_path`.
    """
    import tempfile

    from hawk.runner import refresh_token as hawk_hook
    from inspect_ai.hooks import _hooks

    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    monkeypatch.setenv("HAWK_RUNNER_REFRESH_URL", f"{hawk.url}/token")
    monkeypatch.setenv("HAWK_RUNNER_REFRESH_CLIENT_ID", "client")
    monkeypatch.setenv("HAWK_RUNNER_REFRESH_TOKEN", "refresh-0")
    monkeypatch.setenv("AI_GATEWAY_BASE_URL", f"{hawk.url}/middleman")
    monkeypatch.setenv("BASE_API_KEY", "startup-jwt")
    monkeypatch.delenv("HAWK_ACCESS_TOKEN", raising=False)
    hook = hawk_hook.RefreshTokenHook()
    # how a runner's hooks are found: the one registry Inspect consults
    monkeypatch.setattr(_hooks, "get_all_hooks", lambda: [hook])
    return hook


class ThreadedHawk:
    """A FakeHawk on its own event loop in a background thread.

    For tests that drive our code with `asyncio.run`: each run gets a fresh loop, and
    this server outlives them all. Its controls (`finish`, `stuck`, ...) are plain
    dictionary updates, safe to call from the test's thread between requests.
    """

    def __init__(self, **kwargs: Any) -> None:
        import asyncio
        import threading

        self.hawk = FakeHawk(**kwargs)
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._loop.run_forever, daemon=True)
        self._thread.start()
        asyncio.run_coroutine_threadsafe(self.hawk.__aenter__(), self._loop).result(30)

    def close(self) -> None:
        import asyncio

        asyncio.run_coroutine_threadsafe(self.hawk.__aexit__(), self._loop).result(30)
        self._loop.call_soon_threadsafe(self._loop.stop)
        self._thread.join(30)
