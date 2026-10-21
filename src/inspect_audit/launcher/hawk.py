"""Delivering an investigation's key on Hawk, and launching it as the investigator user.

The investigator user is a dedicated, non-admin Hawk user whose provider key is assigned
once (its Cognito `custom:custom_key` names `secret_name`); each investigation replaces
that secret's value with its own key. Installing a secret is a middleman admin call made
with the operator's login. The investigation is then submitted with the investigator
user's own tokens, so it, and everything it launches, runs and bills as that user.
"""

import json
import urllib.parse
import urllib.request
from typing import Any

SECRET_NAME = "INVESTIGATOR_OPENROUTER_KEY"


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
        from .._runs import HawkLogin

        return await HawkLogin(self.api_url).access_token()

    async def install(self, operator_token: str, key: str, secret_name: str = SECRET_NAME) -> None:
        """Make `key` the investigator user's provider key: middleman's admin secret store."""
        from hawk.cli import middleman

        path = f"/admin/secrets/provider-keys/{secret_name}"
        await middleman.request("put", path, operator_token, data={"value": key})

    def investigator_access_token(self, refresh_token: str) -> str:
        """Exchange the investigator user's refresh token at Hawk's identity provider."""
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
        """Submit the investigation's eval set as the investigator user; Hawk's id back."""
        from hawk.client import HawkClient
        from hawk.core.types import EvalSetConfig

        token = self.investigator_access_token(refresh_token)
        async with HawkClient(token=token, api_url=self.api_url) as client:
            created = await client.create_eval_set(
                EvalSetConfig.model_validate(config), refresh_token=refresh_token
            )
        return created["eval_set_id"]

    async def running(self, eval_set_id: str, operator_token: str) -> bool:
        """Whether Hawk still runs this investigation: unknown to Hawk counts as not."""
        from hawk.client import HawkClient

        async with HawkClient(token=operator_token, api_url=self.api_url) as client:
            status = await client.get_job_status(eval_set_id)
        return status is not None and str(status.status) not in ("complete", "failed", "deleted")
