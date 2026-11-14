"""A Hawk API on localhost, for testing our Hawk code through Hawk's own client.

Only the endpoints `dispatch.hawk` calls, answered with Hawk's own response models
(`hawk.core.types.monitoring`), so a field Hawk's client starts to require fails here
as it would against the real service. A job is driven by the test (`finish`, `stuck`,
`forget`), and its logs are real `.eval` files served over HTTP like presigned URLs.

    async with FakeHawk() as hawk:
        runs = HawkRuns(HawkLogin(hawk.url))   # with HAWK_ACCESS_TOKEN set
        run_id = await runs.submit(job)
        hawk.finish(run_id, logs=[path], usage={"openai/x": {...}})
"""

import itertools
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from aiohttp import web
from aiohttp.test_utils import TestServer


@dataclass
class FakeJob:
    config: dict[str, Any]
    body: dict[str, Any]
    status: str = "running"
    logs: list[Path] = field(default_factory=list)
    # model -> Inspect ModelUsage fields, as Hawk reports a sample's usage
    usage: dict[str, dict[str, Any]] = field(default_factory=dict)
    samples_done: int = 0
    runner_log: list[str] = field(default_factory=list)
    stopped: bool = False


class FakeHawk:
    def __init__(self, prices: list[dict[str, Any]] | None = None, served: list[str] | None = None):
        self.jobs: dict[str, FakeJob] = {}
        self.prices = prices or []
        # public model names the proxy serves to this caller
        self.served = list(served or [])
        self.tokens_seen: list[str] = []
        self._ids = itertools.count(1)
        self._server: TestServer | None = None

    # --- the test's controls -------------------------------------------------

    def finish(
        self,
        run_id: str,
        logs: list[Path] | None = None,
        usage: dict[str, dict[str, Any]] | None = None,
        samples: int = 1,
        status: str = "complete",
    ) -> None:
        job = self.jobs[run_id]
        job.status, job.logs = status, list(logs or [])
        job.usage, job.samples_done = dict(usage or {}), samples

    def progress(
        self, run_id: str, usage: dict[str, dict[str, Any]], samples_done: int = 0
    ) -> None:
        job = self.jobs[run_id]
        job.usage, job.samples_done = dict(usage), samples_done

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

    def _job(self, request: web.Request) -> FakeJob:
        self.tokens_seen.append(request.headers.get("Authorization", ""))
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
        self.tokens_seen.append(request.headers.get("Authorization", ""))
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
        sample = monitoring.SampleStatus(
            id="1",
            epoch=1,
            phase="complete" if job.samples_done else "running",  # pyright: ignore[reportArgumentType]
            completed=bool(job.samples_done),
            model_usage={
                model: monitoring.SampleModelUsage.model_validate(counts)
                for model, counts in job.usage.items()
            },
        )
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
                        samples=[sample],
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
        run_id = request.query["log_dir"]
        job = self.jobs.get(run_id)
        files = [{"name": f"{run_id}/{p.name}"} for p in (job.logs if job else [])]
        return web.json_response({"files": files})

    async def _download_urls(self, request: web.Request) -> web.Response:
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
        self.tokens_seen.append(request.headers.get("Authorization", ""))
        # like middleman: every model this caller may use, by its public name
        body = await request.json()
        assert body.get("api_key") == request.headers.get("Authorization", "").removeprefix(
            "Bearer "
        )
        return web.json_response([{"name": n} for n in self.served])

    async def _moved(self, request: web.Request) -> web.Response:
        raise web.HTTPFound(f"{self.url}/elsewhere")

    # --- lifecycle -----------------------------------------------------------

    def _app(self) -> web.Application:
        app = web.Application()
        app.router.add_get("/auth/config", self._auth_config)
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
