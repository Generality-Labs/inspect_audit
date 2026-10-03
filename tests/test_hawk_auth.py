from types import SimpleNamespace

import pytest

pytest.importorskip("hawk")

from inspect_audit import _hawk_auth, _jobs


def test_disabled_provider_hook_keeps_runner_control_credentials(monkeypatch):
    import asyncio

    from hawk.cli import config
    from hawk.runner import credential_helper
    from inspect_ai.hooks import _hooks

    for name in [
        "HAWK_REFRESH_TOKEN",
        "HAWK_ACCESS_TOKEN",
        "HAWK_TOKEN_REFRESH_URL",
        "HAWK_TOKEN_REFRESH_CLIENT_ID",
    ]:
        monkeypatch.setenv(name, "")
    monkeypatch.setenv("HAWK_JOB_ID", "direct-audit")
    monkeypatch.setenv("HAWK_RUNNER_REFRESH_TOKEN", "runner-refresh")
    monkeypatch.setattr(_hooks, "override_api_key", lambda *args: None)
    calls = []

    def discover(api, persist):
        calls.append((api, persist))
        return SimpleNamespace(
            token_endpoint="https://auth.test/token", client_id="operator-client"
        )

    monkeypatch.setattr(config, "discover_server_config", discover)
    monkeypatch.setattr(credential_helper, "_get_access_token", lambda: "current-access")
    monkeypatch.setattr(credential_helper, "_current_refresh_token", lambda: "rotated-refresh")
    assert asyncio.run(_jobs.Hawk("https://hawk.test").access_token()) == "current-access"
    assert _hawk_auth.credentials("https://hawk.test") == ("current-access", "rotated-refresh")
    assert calls == [("https://hawk.test", False)]


def test_active_runner_hook_is_preserved(monkeypatch):
    import asyncio

    from inspect_ai.hooks import _hooks

    monkeypatch.setenv("HAWK_JOB_ID", "proxied-audit")
    monkeypatch.setattr(_hooks, "override_api_key", lambda *args: "hook-access")
    monkeypatch.setattr(_hawk_auth, "credentials", lambda *args: pytest.fail("Unexpected fallback"))
    assert asyncio.run(_jobs.Hawk("https://hawk.test").access_token()) == "hook-access"
