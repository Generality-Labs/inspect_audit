"""Hawk control-plane credentials independent of provider-key override hooks."""

import os
from threading import Lock

_lock = Lock()


def credentials(api_url: str) -> tuple[str, str | None]:
    """Use Hawk's native credential cache, including rotated refresh tokens.

    Direct provider calls disable the generic provider-key hook. The runner still
    supplies its OAuth refresh credential; discover missing public endpoint
    settings from the same operator-selected Hawk API. Credentials stay on host.
    """
    from hawk.cli.config import discover_server_config
    from hawk.runner import credential_helper

    with _lock:
        refresh = os.environ.get("HAWK_REFRESH_TOKEN") or os.environ.get(
            "HAWK_RUNNER_REFRESH_TOKEN"
        )
        if refresh:
            if not os.environ.get("HAWK_REFRESH_TOKEN"):
                os.environ["HAWK_REFRESH_TOKEN"] = refresh
            if not os.environ.get("HAWK_TOKEN_REFRESH_URL") or not os.environ.get(
                "HAWK_TOKEN_REFRESH_CLIENT_ID"
            ):
                config = discover_server_config(api_url, persist=False)
                if not config.token_endpoint:
                    raise RuntimeError("Hawk did not advertise its OAuth token endpoint")
                if not os.environ.get("HAWK_TOKEN_REFRESH_URL"):
                    os.environ["HAWK_TOKEN_REFRESH_URL"] = config.token_endpoint
                if not os.environ.get("HAWK_TOKEN_REFRESH_CLIENT_ID"):
                    os.environ["HAWK_TOKEN_REFRESH_CLIENT_ID"] = config.client_id
            token = credential_helper._get_access_token()
            return token, credential_helper._current_refresh_token()
        token = os.environ.get("HAWK_ACCESS_TOKEN")
        if not token:
            raise RuntimeError("Hawk runner has no control-plane credential")
        return token, None
