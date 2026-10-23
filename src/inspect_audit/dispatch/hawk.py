"""Everything that only exists on Hawk: logging in, submitting, and fetching its logs.

Hawk-specific code lives here and nowhere else, so a Hawk upgrade touches one file.
"""

import asyncio
import inspect
import json
import os
import shutil
import sys
import tempfile
import urllib.parse
import urllib.request
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Literal

from .jobs import COMPLETE, FAILED, RUNNING, STUCK, Job, validate_environment


def hawk_config(job: Job, *, restricted: bool = False) -> dict[str, Any]:
    """The eval-set config Hawk runs for a job. Models route through Hawk's proxy."""
    package_name, _, task_name = job.task.partition("/")

    def group(model: str) -> dict[str, Any]:
        configured = job.model_configs.get(model)
        item: dict[str, Any] = {"name": model}
        if configured is not None:
            item["args"] = {
                **configured.args,
                "config": configured.config.model_dump(exclude_none=True),
                "base_url": configured.base_url,
            }
        return {"package": "inspect-ai", "items": [item]}

    item: dict[str, Any] = {"name": task_name, "args": job.task_args}
    if job.sample_ids is not None:
        item["sample_ids"] = job.sample_ids
    limit = [job.sample_range[0] - 1, job.sample_range[1]] if job.sample_range else None
    config: dict[str, Any] = {
        "name": job.label,
        "limit": limit,
        "packages": [job.package, *job.extra_packages, "inspect-ai==0.3.266"],
        "tasks": [{"package": job.package, "name": package_name, "items": [item]}],
        "models": [group(m) for m in job.models],
        "model_roles": {role: group(m) for role, m in job.model_roles.items()} or None,
        "epochs": job.epochs,
        # a failed child is reported, not retried behind the auditor's back
        "retry_attempts": 0,
        "runner": {"environment": validate_environment(job.environment)},
    }
    if restricted:
        # Judges selected through task arguments are outside the eval model grid.
        judges = (
            (job.task_args.get("judge") or {}) if job.task == "inspect_audit/investigation" else {}
        )
        if judges:
            config["additional_models"] = [group(str(m)) for m in dict.fromkeys(judges.values())]
        sources = (
            (job.task_args.get("logs") or []) if job.task == "inspect_audit/investigation" else []
        )
        if any(not str(source).startswith("hawk:") for source in sources):
            raise ValueError("restricted investigation jobs require Hawk transcript sources")
        ids = list(dict.fromkeys(s.removeprefix("hawk:") for s in sources))
        if any(not sid or "," in sid for sid in ids):
            raise ValueError("declare each Hawk transcript source as a separate hawk:<id>")
        if ids:
            config["transcripts"] = {"sources": [{"eval_set_id": sid} for sid in ids]}
    return config


class HawkLogin:
    """Hawk credentials for whoever this process runs as.

    Inside a Hawk runner, the runner's own refresh hook. Elsewhere, the operator's
    `hawk` login: only the `hawk` binary that wrote it can read it from the keychain
    without asking, so that binary is asked, past the venv's copy `uv run` puts
    first on PATH.
    """

    def __init__(self, api_url: str) -> None:
        self.api_url = api_url
        venv = Path(sys.executable).with_name("hawk")
        others = [
            found
            for directory in os.environ.get("PATH", "").split(os.pathsep)
            if (found := shutil.which("hawk", path=directory))
            and Path(found).resolve() != venv.resolve()
        ]
        self.binary = others[0] if others else "hawk"

    async def _hawk(self, *args: str) -> str:
        from inspect_ai.util import subprocess as run

        result = await run(
            [self.binary, *args], text=True, env={"HAWK_API_URL": self.api_url}, timeout=60
        )
        if not result.success:
            raise RuntimeError(f"hawk {' '.join(args)} failed: {result.stderr[-1500:]}")
        return str(result.stdout).strip()

    @staticmethod
    def _runner_hook() -> Any:
        """Hawk's token refresh hook, when this process runs inside a Hawk runner."""
        from inspect_ai.hooks._hooks import get_all_hooks

        return next(
            (h for h in get_all_hooks() if type(h).__module__ == "hawk.runner.refresh_token"),
            None,
        )

    async def access_token(self) -> str:
        if self._runner_hook() is not None:
            from inspect_ai.hooks._hooks import override_api_key

            # asked with the runner's own gateway credential: hawk >= 3.7 answers an
            # empty ask only for a provider it routed through its gateway
            token = override_api_key("HAWK_ACCESS_TOKEN", os.environ.get("BASE_API_KEY", ""))
            if not token:
                raise RuntimeError("Hawk runner's token refresh hook gave no token")
            return token
        # fetched each time, not cached: the login refreshes it, a copy expires
        return await self._hawk("auth", "access-token")

    async def refresh_token(self) -> str:
        """The credential a child job's runner renews its access with: this user's."""
        # access first: refreshing it may rotate the hook's refresh token
        if (hook := self._runner_hook()) is not None:
            token = getattr(hook, "_current_refresh_token", None)
        else:
            token = await self._hawk("auth", "refresh-token")
        if not token:
            raise RuntimeError("no Hawk refresh credential for child jobs: run `hawk login`")
        return token


def hawk_supports_restricted() -> bool:
    from hawk.client import HawkClient
    from hawk.core.types import EvalSetConfig

    return "credential_mode" in inspect.signature(HawkClient.create_eval_set).parameters and {
        "transcripts",
        "additional_models",
    }.issubset(EvalSetConfig.model_fields)


class HawkRuns:
    """Jobs on Hawk, through `hawk.client.HawkClient`."""

    def __init__(
        self,
        api_url: str,
        *,
        credential_mode: Literal["restricted", "user"] = "user",
        secrets: dict[str, str] | None = None,
    ) -> None:
        self.login = HawkLogin(api_url)
        self.credential_mode = credential_mode
        self.secrets = dict(secrets or {})
        if credential_mode == "restricted" and not hawk_supports_restricted():
            # refused now, not at the first child job an hour into an audit
            raise RuntimeError(
                "restricted credentials need Hawk 4 (restricted children, declared "
                "transcripts); this Hawk client predates them: declare credential_mode: user"
            )

    @asynccontextmanager
    async def _client(self) -> AsyncGenerator[Any]:
        from hawk.client import HawkClient

        async with HawkClient(
            token=await self.login.access_token(), api_url=self.login.api_url
        ) as client:
            yield client

    async def submit(self, job: Job) -> str:
        from hawk.client import HawkClient
        from hawk.core.types import EvalSetConfig

        restricted = self.credential_mode == "restricted"
        settings = hawk_config(job, restricted=restricted)
        # the job's own code may read Hawk (an investigation fetching hawk: logs); Hawk does
        # not tell a runner where its API is
        settings["runner"]["environment"]["HAWK_API_URL"] = self.login.api_url
        config = EvalSetConfig.model_validate(settings)
        # the access token first: refreshing it may rotate the refresh token
        token = await self.login.access_token()
        async with HawkClient(token=token, api_url=self.login.api_url) as client:
            if restricted:
                # No fallback: old clients/deployments must refuse this mode.
                options: dict[str, Any] = {"credential_mode": "restricted"}
                if self.secrets:
                    options["secrets"] = self.secrets
                created = await client.create_eval_set(config, **options)
            else:
                refresh_token = await self.login.refresh_token()
                options = {"refresh_token": refresh_token}
                if self.secrets:
                    options["secrets"] = self.secrets
                created = await client.create_eval_set(config, **options)
        return created["eval_set_id"]

    def address(self, run_id: str) -> str:
        return f"hawk:{run_id}"

    async def status(self, run_id: str) -> str:
        async with self._client() as client:
            found = await client.get_job_status(run_id)
        if found is None:
            return FAILED
        status = str(found.status)
        if status == "complete":
            return COMPLETE
        if status in ("failed", "deleted"):
            return FAILED
        if status == "stuck":
            return STUCK
        return RUNNING

    async def logs(self, run_id: str, out_dir: Path) -> list[Path]:
        from hawk.cli.download import download_file

        out_dir.mkdir(parents=True, exist_ok=True)
        files: list[Path] = []
        async with self._client() as client:
            names = [str(f["name"]) for f in await client.get_log_files(run_id)]
            names = [n for n in names if n.endswith(".eval")]
            async for url, filename in client.get_download_urls(names):
                dest = out_dir / Path(filename).name
                await download_file(url, dest)
                files.append(dest)
        return sorted(files)

    async def runner_log(self, run_id: str) -> str:
        async with self._client() as client:
            entries = await client.fetch_logs(run_id, limit=120)
        lines = [
            f"[{e.timestamp:%H:%M:%S}] {e.message}"
            for e in sorted(entries, key=lambda e: e.timestamp)
        ]
        return "\n".join(lines)[-6000:] or "(no runner log yet)"

    async def stop(self, run_id: str) -> None:
        async with self._client() as client:
            await client.stop_eval_set(run_id)


# Launcher delivery: installing an audit's key and submitting as the auditor user

SECRET_NAME = "AUDITOR_OPENROUTER_KEY"


class HawkDelivery:
    def __init__(self) -> None:
        # the hawk CLI's own settings: environment, ~/.config/hawk-cli/env, then the
        # API's /auth/config for whatever is not set locally (middleman's URL)
        from hawk.cli.config import discover_server_config, get_config

        config = get_config()
        self.api_url = config.require_api_url().rstrip("/")
        if not config.middleman_url:
            raise RuntimeError("Hawk did not say where middleman is: set HAWK_MIDDLEMAN_URL")
        # cached per API, so a second deployment's identity provider is never used here
        self.server = discover_server_config(self.api_url)

    async def operator_token(self) -> str:
        """The operator's own access token: an admin, needed to install the key."""
        return await HawkLogin(self.api_url).access_token()

    async def install(self, operator_token: str, key: str, secret_name: str = SECRET_NAME) -> None:
        """Make `key` the auditor user's provider key: middleman's admin secret store."""
        from hawk.cli import middleman

        path = f"/admin/secrets/provider-keys/{secret_name}"
        await middleman.request("put", path, operator_token, data={"value": key})

    def auditor_access_token(self, refresh_token: str) -> str:
        """Exchange the auditor user's refresh token at Hawk's identity provider."""
        body = urllib.parse.urlencode(
            {
                "grant_type": "refresh_token",
                "client_id": self.server.client_id,
                "refresh_token": refresh_token,
            }
        ).encode()
        request = urllib.request.Request(
            self.server.token_endpoint,
            data=body,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        with urllib.request.urlopen(request, timeout=60) as response:
            return str(json.load(response)["access_token"])

    async def submit(self, config: dict[str, Any], refresh_token: str) -> str:
        """Submit the audit's eval set as the auditor user; Hawk's id back."""
        from hawk.client import HawkClient
        from hawk.core.types import EvalSetConfig

        token = self.auditor_access_token(refresh_token)
        async with HawkClient(token=token, api_url=self.api_url) as client:
            created = await client.create_eval_set(
                EvalSetConfig.model_validate(config), refresh_token=refresh_token
            )
        return created["eval_set_id"]

    async def running(self, eval_set_id: str, operator_token: str) -> bool:
        """Whether Hawk still runs this audit: unknown to Hawk counts as not."""
        from hawk.client import HawkClient

        async with HawkClient(token=operator_token, api_url=self.api_url) as client:
            status = await client.get_job_status(eval_set_id)
        return status is not None and str(status.status) not in ("complete", "failed", "deleted")


def fetch_eval_sets(eval_sets: str) -> str:
    """Download eval logs from the Hawk warehouse to a temporary directory.

    Hawk's API is HAWK_API_URL; the token is HAWK_ACCESS_TOKEN when set, else this
    process's own Hawk credentials (`HawkLogin`): the runner's hook, which a
    Hawk runner installs before it loads tasks, or the operator's `hawk` login.
    """
    import concurrent.futures

    from hawk.cli.download import download_file
    from hawk.client import HawkClient

    fetched = Path(tempfile.mkdtemp(prefix="hawk_logs_"))
    sets = eval_sets.split(",")

    api = os.environ.get("HAWK_API_URL")
    if not api:
        raise ValueError("fetching hawk: logs needs HAWK_API_URL, Hawk's API address")

    async def fetch() -> None:
        token = os.environ.get("HAWK_ACCESS_TOKEN") or await HawkLogin(api).access_token()
        async with HawkClient(token=token, api_url=api) as client:
            for address in sets:
                eval_set, _, selected_file = address.partition("/")
                files = await client.get_log_files(eval_set)
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
                async for url, filename in client.get_download_urls(names):
                    await download_file(url, fetched / f"{prefix}{Path(filename).name}")

    # task construction may already be inside inspect's event loop
    with concurrent.futures.ThreadPoolExecutor(1) as pool:
        pool.submit(asyncio.run, fetch()).result()
    return str(fetched)


def declared_transcripts() -> dict[str, str] | None:
    """The eval sets Hawk resolved for this run (Hawk 4 `transcripts`), else None."""
    try:
        from hawk.runtime import transcript_sources  # pyright: ignore[reportMissingImports]
    except ImportError:
        if "HAWK_TRANSCRIPT_SOURCES" in os.environ:
            raise
        return None  # a Hawk without declared inputs
    if "HAWK_TRANSCRIPT_SOURCES" not in os.environ:
        return None
    return {s.eval_set_id: s.location for s in transcript_sources()}  # pyright: ignore[reportUnknownVariableType,reportUnknownMemberType]
