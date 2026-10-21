"""Delivering an investigation's key on Hawk, and launching it as the investigator user.

The investigator user is a dedicated, non-admin Hawk user whose provider key is assigned
once (its Cognito `custom:custom_key` names `secret_name`); each investigation replaces
that secret's value with its own key. Installing a secret is a middleman admin call made
with the operator's login. The investigation is then submitted with the investigator
user's own tokens, so it, and everything it launches, runs and bills as that user.
"""

import json
import os
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

SECRET_NAME = "INVESTIGATOR_OPENROUTER_KEY"


def _cli_settings() -> dict[str, str]:
    """`~/.config/hawk-cli/env`, as the hawk CLI reads it, overridden by the environment."""
    settings: dict[str, str] = {}
    path = Path.home() / ".config" / "hawk-cli" / "env"
    if path.is_file():
        for line in path.read_text().splitlines():
            key, sep, value = line.partition("=")
            if sep and key.strip() and not key.startswith("#"):
                settings[key.strip()] = value.strip()
    return {**settings, **{k: v for k, v in os.environ.items() if k.startswith("HAWK_")}}


def server_config(api_url: str) -> dict[str, Any]:
    """The cached server config the hawk CLI saved for exactly this API's host.

    Matched by host: a machine that has used two Hawk deployments holds two configs,
    with different token endpoints, and the wrong one would log the user in elsewhere.
    """
    host = urllib.parse.urlparse(api_url).netloc
    cache = Path.home() / ".config" / "hawk-cli" / "server-config-cache"
    matches = sorted(cache.glob(f"{host}-*.json")) if cache.is_dir() else []
    if len(matches) != 1:
        raise RuntimeError(
            f"expected one cached hawk server config for {host}, found {len(matches)}: "
            "run `hawk login` against it once"
        )
    return dict(json.loads(matches[0].read_text()))


class HawkDelivery:
    def __init__(self, api_url: str | None = None, middleman_url: str | None = None) -> None:
        settings = _cli_settings()
        self.api_url = (api_url or settings.get("HAWK_API_URL") or "").rstrip("/")
        self.middleman_url = (middleman_url or settings.get("HAWK_MIDDLEMAN_URL") or "").rstrip("/")
        if not self.api_url or not self.middleman_url:
            raise RuntimeError("HAWK_API_URL and HAWK_MIDDLEMAN_URL are needed to launch on Hawk")

    async def operator_token(self) -> str:
        """The operator's own access token: an admin, needed to install the key."""
        from .._runs import HawkLogin

        return await HawkLogin(self.api_url).access_token()

    def install(self, operator_token: str, key: str, secret_name: str = SECRET_NAME) -> None:
        """Make `key` the investigator user's provider key: middleman's admin secret store."""
        request = urllib.request.Request(
            f"{self.middleman_url}/admin/secrets/provider-keys/{secret_name}",
            data=json.dumps({"value": key}).encode(),
            headers={
                "Authorization": f"Bearer {operator_token}",
                "Content-Type": "application/json",
            },
            method="PUT",
        )
        with urllib.request.urlopen(request, timeout=60):
            pass

    def investigator_access_token(self, refresh_token: str) -> str:
        """Exchange the investigator user's refresh token at Hawk's identity provider."""
        config = server_config(self.api_url)
        body = urllib.parse.urlencode(
            {
                "grant_type": "refresh_token",
                "client_id": config["client_id"],
                "refresh_token": refresh_token,
            }
        ).encode()
        request = urllib.request.Request(
            config["token_endpoint"],
            data=body,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        with urllib.request.urlopen(request, timeout=60) as response:
            return str(json.load(response)["access_token"])

    async def submit(self, config: dict[str, Any], refresh_token: str) -> str:
        """Submit the investigation's eval set as the investigator user; Hawk's id back."""
        from hawk.client import HawkClient
        from hawk.core.types import EvalSetConfig

        token = self.investigator_access_token(refresh_token)
        async with HawkClient(token=token, api_url=self.api_url) as client:
            created: Any = await client.create_eval_set(
                EvalSetConfig.model_validate(config), refresh_token=refresh_token
            )
        if isinstance(created, dict):
            return str(created["eval_set_id"])
        return str(getattr(created, "eval_set_id", created))

    async def running(self, eval_set_id: str, operator_token: str) -> bool:
        """Whether Hawk still runs this investigation: unknown to Hawk counts as not."""
        from hawk.client import HawkClient

        async with HawkClient(token=operator_token, api_url=self.api_url) as client:
            status = await client.get_job_status(eval_set_id)
        return status is not None and str(status.status) not in ("complete", "failed", "deleted")
