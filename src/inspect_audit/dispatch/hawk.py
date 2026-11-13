"""Everything that only exists on Hawk: logging in, submitting, and fetching its logs.

Hawk-specific code lives here and nowhere else, so a Hawk upgrade touches one file.
"""

import asyncio
import inspect
import json
import os
import shutil
import sys
import urllib.parse
import urllib.request
from collections.abc import AsyncGenerator, Generator
from contextlib import asynccontextmanager, contextmanager
from pathlib import Path
from typing import Any, Literal

from .jobs import (
    FINISHED,
    INSPECT_VERSION,
    Job,
    JobKind,
    JobState,
    add_usage,
    validate_environment,
)
from .jobs import package_name as distribution_name


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
        "packages": [
            job.package,
            *job.extra_packages,
            *(
                []
                if any(distribution_name(p) == "inspect-ai" for p in job.extra_packages)
                else [f"inspect-ai=={INSPECT_VERSION}"]
            ),
        ],
        "tasks": [{"package": job.package, "name": package_name, "items": [item]}],
        "models": [group(m) for m in job.models],
        "model_roles": {role: group(m) for role, m in job.model_roles.items()} or None,
        "epochs": job.epochs,
        # a failed child is reported, not retried behind the auditor's back
        "retry_attempts": 0,
        "runner": {"environment": validate_environment(job.environment)},
    }
    # Judges selected through task arguments are outside the eval model grid: declared
    # here, in either credential mode, so Hawk gives the job their provider's credentials
    # (smoke round 7: an investigation's OpenRouter judge failed to load, "No
    # OPENROUTER_API_KEY defined", under user credentials; FRICTION 94)
    investigation = JobKind.of(job.task) is JobKind.INVESTIGATION
    judges = (job.task_args.get("judge") or {}) if investigation else {}
    if judges:
        config["additional_models"] = [group(str(m)) for m in dict.fromkeys(judges.values())]
    if restricted:
        sources = (job.task_args.get("logs") or []) if investigation else []
        if any(not str(source).startswith("hawk:") for source in sources):
            raise ValueError("restricted investigation jobs require Hawk transcript sources")
        ids = list(dict.fromkeys(s.removeprefix("hawk:") for s in sources))
        if any(not sid or "," in sid for sid in ids):
            raise ValueError("declare each Hawk transcript source as a separate hawk:<id>")
        if ids:
            config["transcripts"] = {"sources": [{"eval_set_id": sid} for sid in ids]}
    return config


def hawk_state(word: Any) -> JobState:
    """Hawk's word for a job's status, in ours: the one place Hawk's vocabulary is read.

    A job Hawk does not know (None) has failed; one it deleted has failed; anything not
    yet finished or stuck is running.
    """
    return {
        None: JobState.FAILED,
        "complete": JobState.COMPLETE,
        "failed": JobState.FAILED,
        "deleted": JobState.FAILED,
        "stuck": JobState.STUCK,
    }.get(None if word is None else str(word), JobState.RUNNING)


async def submit_eval_set(
    config: dict[str, Any],
    *,
    api_url: str,
    token: str,
    credential_mode: Literal["restricted", "user"],
    refresh_token: str | None = None,
    secrets: dict[str, str] | None = None,
) -> str:
    """Every submission to Hawk goes through here (FRICTION 86); Hawk's eval set id back.

    The credential mode is always stated: left out, Hawk uses a deployment feature flag.
    A user-mode job renews with `refresh_token`; a restricted one carries no login. The
    runner is told where Hawk's API is (Hawk does not), for jobs that read Hawk.
    """
    from hawk.client import HawkClient
    from hawk.core.types import EvalSetConfig

    if (credential_mode == "user") != (refresh_token is not None):
        raise ValueError("a user-mode job needs a refresh token; a restricted one takes none")
    config = json.loads(json.dumps(config))
    runner = config.setdefault("runner", None) or {}
    config["runner"] = runner
    environment = runner.get("environment") or {}
    runner["environment"] = {**environment, "HAWK_API_URL": api_url}
    options: dict[str, Any] = {"credential_mode": credential_mode}
    if refresh_token is not None:
        options["refresh_token"] = refresh_token
    if secrets:
        options["secrets"] = secrets
    async with HawkClient(token=token, api_url=api_url) as client:
        created = await client.create_eval_set(EvalSetConfig.model_validate(config), **options)
    return str(created["eval_set_id"])


class HawkLogin:
    """Hawk credentials for whoever this process runs as: the one place a token is found.

    Inside a Hawk runner, the runner's own refresh hook. Elsewhere, `HAWK_ACCESS_TOKEN`
    when the operator sets one (a CI job, a borrowed token), else the operator's
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

    @staticmethod
    def _user_credentials(hook: Any) -> Any:
        """The user credentials a runner's hook renews with; None under restricted mode.

        Hawk 4 keeps them as a `human_token.Config` on its RefreshTokenHook; the
        RestrictedTokenHook holds an execution credential that cannot launch jobs.
        """
        from hawk.runner import human_token

        config = getattr(hook, "config", None)
        return config if isinstance(config, human_token.Config) else None

    async def access_token(self) -> str:
        if (hook := self._runner_hook()) is not None:
            from hawk.runner import human_token

            if (config := self._user_credentials(hook)) is None:
                raise RuntimeError("a restricted Hawk runner has no user token to launch jobs")
            # Hawk 4's hook answers from its cache and refreshes in the background, so
            # an ask can come back empty; this waits for a usable token instead
            return await asyncio.to_thread(
                human_token.get_access_token,
                config,
                refresh_margin_seconds=hook.refresh_margin_seconds,
            )
        if token := os.environ.get("HAWK_ACCESS_TOKEN"):
            return token
        # fetched each time, not cached: the login refreshes it, a copy expires
        return await self._hawk("auth", "access-token")

    async def refresh_token(self) -> str:
        """The credential a child job's runner renews its access with: this user's."""
        if (hook := self._runner_hook()) is not None:
            from hawk.runner import human_token

            if (config := self._user_credentials(hook)) is None:
                raise RuntimeError("a restricted Hawk runner has no refresh credential")
            # access first: renewing it may rotate the refresh token, which Hawk 4
            # writes only to its file cache (no public accessor: private read)
            await self.access_token()
            cache = human_token._read(human_token._path(config), config)  # pyright: ignore[reportPrivateUsage]
            token = (cache.refresh_token if cache else config.refresh_token).get_secret_value()
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
        self.credential_mode: Literal["restricted", "user"] = credential_mode
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
        restricted = self.credential_mode == "restricted"
        # the access token first: refreshing it may rotate the refresh token
        token = await self.login.access_token()
        return await submit_eval_set(
            hawk_config(job, restricted=restricted),
            api_url=self.login.api_url,
            token=token,
            credential_mode=self.credential_mode,
            refresh_token=None if restricted else await self.login.refresh_token(),
            secrets=self.secrets,
        )

    async def unservable(self, models: list[str]) -> list[str]:
        """Which of `models` Hawk would refuse in a job: names its proxy does not serve.

        Hawk checks a job's models when it is submitted (FRICTION 53), by the public
        name its proxy knows each one as: `registry_model_name`, so `openai/gpt-5.1`
        is looked up as `gpt-5.1` (FRICTION 71). This is the same check, made before
        any spend; the names returned are the ones the caller gave.
        """
        from hawk.core.providers import registry_model_name

        token = await self.login.access_token()
        served = await middleman_request(
            self.login.api_url, "post", "/permitted_models_info", token, {"api_key": token}
        )
        names = {str(m.get("name")) for m in served if not m.get("dead")}
        return sorted(m for m in set(models) if registry_model_name(m, strict=False) not in names)

    def address(self, run_id: str) -> str:
        return f"hawk:{run_id}"

    async def status(self, run_id: str) -> JobState:
        async with self._client() as client:
            found = await client.get_job_status(run_id)
        return hawk_state(found.status if found is not None else None)

    async def usage(self, run_id: str) -> tuple[dict[str, Any], int]:
        """From Hawk's live status: each sample's usage so far (its summary's once done)."""
        async with self._client() as client:
            status = await client.get_eval_set_status(run_id)
        total: dict[str, Any] = {}
        done = 0
        for task in status.tasks:
            for sample in task.samples or []:
                done += bool(sample.completed)
                add_usage(total, {m: c.model_dump() for m, c in (sample.model_usage or {}).items()})
        return total, done

    async def logs(self, run_id: str, out_dir: Path) -> list[Path]:
        return await download(run_id, await self.login.access_token(), out_dir, self.login.api_url)

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
        path = f"/admin/secrets/provider-keys/{secret_name}"
        await middleman_request(self.api_url, "put", path, operator_token, {"value": key})

    def auditor_tokens(self, refresh_token: str) -> tuple[str, str]:
        """Exchange the auditor user's refresh token: (access token, refresh token to use).

        An identity provider that rotates refresh tokens retires the one it was given,
        so the job must be handed the new one, and the stored one no longer works.
        """
        from hawk.cli.util.auth import TokenResponse

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
            reply = TokenResponse.model_validate(json.load(response))
        current = reply.refresh_token or refresh_token
        if current != refresh_token:
            print(
                "warning: the identity provider rotated the auditor user's refresh token; "
                "the stored one no longer works: store the new one before the next launch",
                file=sys.stderr,
            )
        return reply.access_token, current

    async def submit(self, config: dict[str, Any], refresh_token: str) -> str:
        """Submit the audit's eval set as the auditor user; Hawk's id back."""
        token, refresh_token = self.auditor_tokens(refresh_token)
        # the audit itself runs as the auditor user: it needs that user's token to
        # launch its (restricted) children (FRICTION 79d)
        return await submit_eval_set(
            config,
            api_url=self.api_url,
            token=token,
            credential_mode="user",
            refresh_token=refresh_token,
        )

    async def running(self, eval_set_id: str, operator_token: str) -> bool | None:
        """Whether Hawk still runs this audit; None when Hawk does not say.

        Only a status Hawk reports as finished means not running. Unknown to Hawk, or a
        refused or failed lookup, is not evidence it stopped: `reap` must not disable a
        live audit's key on a transient 404 or 403 (FRICTION 79e).
        """
        from hawk.client import HawkClient

        try:
            async with HawkClient(token=operator_token, api_url=self.api_url) as client:
                status = await client.get_job_status(eval_set_id)
        except Exception:
            return None
        if status is None:
            return None
        return hawk_state(status.status) not in FINISHED


async def middleman_request(
    api_url: str, method: str, path: str, token: str, data: dict[str, Any] | None = None
) -> Any:
    """A JSON call to the Hawk deployment's LLM proxy (middleman) with a supplied token.

    Hawk 4's `hawk.cli.middleman.request` refuses any token this process did not read
    from a saved login (FRICTION 70); ours come from `hawk auth access-token` or a
    runner's hook. The proxy's address is the one `api_url` reports, as the CLI
    finds it, and, like Hawk, redirects are not followed with the credential.
    """
    import aiohttp
    import hawk.core.version
    from hawk.cli.config import get_config

    with _api_url(api_url):
        base = get_config().middleman_url
    if not base:
        raise RuntimeError(f"Hawk at {api_url} reports no LLM proxy (middleman) address")
    headers = {
        "Authorization": f"Bearer {token}",
        hawk.core.version.CLIENT_VERSION_HEADER: hawk.core.version.get_version(),
    }
    timeout = aiohttp.ClientTimeout(total=60)
    async with (
        aiohttp.ClientSession(timeout=timeout) as session,
        session.request(
            method, base.rstrip("/") + path, json=data, headers=headers, allow_redirects=False
        ) as response,
    ):
        if response.status >= 300:
            body = (await response.text())[:500]
            raise RuntimeError(f"middleman {method.upper()} {path}: {response.status} {body}")
        return await response.json()


async def prices(api_url: str) -> list[dict[str, Any]]:
    """What Hawk's proxy bills each model at: `GET /usage/prices`, per million tokens.

    `HawkClient` has no method for it yet, so this uses its generic JSON request.
    """
    from hawk.client import HawkClient

    login = HawkLogin(api_url)
    async with HawkClient(token=await login.access_token(), api_url=api_url) as client:
        rows = await client._request_json("GET", "/usage/prices")  # pyright: ignore[reportPrivateUsage]
    if not isinstance(rows, list):
        raise RuntimeError(f"Hawk at {api_url} returned no price list")
    return [r for r in rows if isinstance(r, dict)]


async def download(address: str, token: str, out_dir: Path, api_url: str) -> list[Path]:
    """An eval set's logs into `out_dir`: `<eval set>` or `<eval set>/<file>`.

    Through `HawkClient`, which takes the token it is given. Hawk 4's CLI downloader
    refuses any token this process did not read from a saved login (FRICTION 70),
    and ours come from `hawk auth access-token` or a runner's hook. An eval set that
    wrote no logs (a run that failed before writing any) comes back as an empty list.
    """
    from hawk.cli.download import download_file
    from hawk.client import HawkClient

    eval_set, _, selected = address.partition("/")
    async with HawkClient(token=token, api_url=api_url) as client:
        names = [str(f["name"]) for f in await client.get_log_files(eval_set)]
        if selected:
            names = [n for n in names if Path(n).name == selected]
            if len(names) != 1:
                raise ValueError(
                    f"Expected exactly one Hawk log at {address!r}, found {len(names)}"
                )
        if not names:
            return []
        out_dir.mkdir(parents=True, exist_ok=True)
        async for url, filename in client.get_download_urls(names):
            await download_file(url, out_dir / Path(filename).name)
    return sorted(out_dir.glob("*.eval"))


@contextmanager
def _api_url(api_url: str) -> Generator[None]:
    """Hawk's CLI helpers read the API address from the environment: set it for a call."""
    previous = os.environ.get("HAWK_API_URL")
    os.environ["HAWK_API_URL"] = api_url
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop("HAWK_API_URL", None)
        else:
            os.environ["HAWK_API_URL"] = previous


def fetch_eval_sets(eval_sets: str) -> str:
    """Download eval sets from the Hawk warehouse to a temporary directory, one folder each.

    Hawk's API is HAWK_API_URL; the token is this process's own (`HawkLogin`): the
    runner's hook, which a Hawk runner installs before it loads tasks, or the operator's.
    """
    import concurrent.futures

    api = os.environ.get("HAWK_API_URL")
    if not api:
        raise ValueError("fetching hawk: logs needs HAWK_API_URL, Hawk's API address")
    from .._paths import stable_dir

    fetched = stable_dir("hawk", eval_sets)

    async def fetch() -> None:
        token = await HawkLogin(api).access_token()
        for address in eval_sets.split(","):
            # a folder per eval set: same-named files from two sets cannot collide
            files = await download(address, token, fetched / address.partition("/")[0], api)
            if not files:
                raise ValueError(f"No .eval files found in Hawk eval set {address!r}")

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
