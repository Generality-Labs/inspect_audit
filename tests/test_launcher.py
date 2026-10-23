"""The launcher: a capped key per audit, revoked in every case."""

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from inspect_audit.dispatch import secrets
from inspect_audit.dispatch.launcher import Launcher, Records
from inspect_audit.dispatch.openrouter import KeyState


class FakeKeys:
    """OpenRouter's key API as a dict: what was minted, spent and disabled."""

    def __init__(self) -> None:
        self.keys: dict[str, dict[str, Any]] = {}
        self.disabled_calls: list[str] = []

    def create(self, name: str, limit: float) -> tuple[str, str]:
        key_hash = f"h{len(self.keys)}"
        self.keys[key_hash] = {"name": name, "limit": limit, "usage": 0.0, "disabled": False}
        return key_hash, f"sk-or-secret-{key_hash}"

    def get(self, key_hash: str) -> KeyState:
        k = self.keys[key_hash]
        return KeyState(
            key_hash, k["name"], k["limit"], k["limit"] - k["usage"], k["usage"], k["disabled"]
        )

    def list(self, prefix: str) -> list[KeyState]:
        return [self.get(h) for h, k in self.keys.items() if k["name"].startswith(prefix)]

    def disable(self, key_hash: str) -> None:
        self.disabled_calls.append(key_hash)
        self.keys[key_hash]["disabled"] = True


class FakeHawk:
    def __init__(self, fail_at: str | None = None) -> None:
        self.installed: list[str] = []
        self.submitted: list[dict[str, Any]] = []
        self.live: set[str] = set()
        self.fail_at = fail_at

    async def operator_token(self) -> str:
        return "operator-admin-token"

    async def install(self, operator_token: str, key: str) -> None:
        if self.fail_at == "install":
            raise RuntimeError("middleman refused")
        self.installed.append(key)

    async def submit(self, config: dict[str, Any], refresh_token: str) -> str:
        if self.fail_at == "submit":
            raise RuntimeError("hawk refused the config")
        assert refresh_token == "auditor-refresh"
        self.submitted.append(config)
        self.live.add(config["eval_set_id"])
        return str(config["eval_set_id"])

    async def running(self, eval_set_id: str, operator_token: str) -> bool:
        return eval_set_id in self.live


def _launcher(tmp_path: Path, hawk: FakeHawk | None = None) -> tuple[Launcher, FakeKeys, FakeHawk]:
    keys, hawk = FakeKeys(), hawk or FakeHawk()
    launcher = Launcher(keys, hawk, Records(tmp_path), lambda: "auditor-refresh")
    return launcher, keys, hawk


def test_an_audit_gets_its_own_capped_key_installed_then_launched(tmp_path: Path) -> None:
    launcher, keys, hawk = _launcher(tmp_path)
    record = asyncio.run(launcher.start({"name": "inv"}, 20.0, "simpleqa"))
    assert (
        record.id.startswith(f"{Records(tmp_path).prefix}simpleqa-") and record.stage == "running"
    )
    [key] = keys.keys.values()
    assert (key["name"], key["limit"]) == (record.id, 20.0)
    assert hawk.installed == ["sk-or-secret-h0"]
    assert hawk.submitted[0]["eval_set_id"] == record.id
    # the secret is never written to the record on disk
    assert "sk-or-secret" not in (tmp_path / f"{record.id}.json").read_text()


@pytest.mark.parametrize("stage", ["install", "submit"])
def test_a_launch_that_fails_after_minting_revokes_the_key_at_once(
    tmp_path: Path, stage: str
) -> None:
    launcher, keys, _ = _launcher(tmp_path, FakeHawk(fail_at=stage))
    with pytest.raises(RuntimeError):
        asyncio.run(launcher.start({}, 5.0, "x"))
    [record] = Records(tmp_path).all()
    assert record.stage == "finished" and "launch failed" in record.note
    assert [k["disabled"] for k in keys.keys.values()] == [True]


def test_a_crash_before_the_keys_id_is_written_is_recovered_by_name(tmp_path: Path) -> None:
    launcher, keys, _ = _launcher(tmp_path)
    records = Records(tmp_path)
    # the state a crash leaves: planned on disk, key minted at OpenRouter, hash unrecorded
    from inspect_audit.dispatch.launcher import Record

    records.save(Record(id="inv-lost-0001", cap_usd=3.0))
    keys.create("inv-lost-0001", 3.0)
    keys.keys["h0"]["usage"] = 1.25
    record = asyncio.run(launcher.finish("inv-lost-0001"))
    assert (record.key_hash, record.usage_usd, record.stage) == ("h0", 1.25, "finished")
    assert keys.keys["h0"]["disabled"]


def test_finish_records_the_spend_and_can_be_repeated(tmp_path: Path) -> None:
    launcher, keys, _ = _launcher(tmp_path)
    record = asyncio.run(launcher.start({}, 10.0, "y"))
    keys.keys[record.key_hash]["usage"] = 7.5  # type: ignore[index]
    once = asyncio.run(launcher.finish(record.id))
    twice = asyncio.run(launcher.finish(record.id))
    assert once.usage_usd == twice.usage_usd == 7.5
    assert keys.disabled_calls == [record.key_hash]  # disabled once, not twice
    assert once.finished == twice.finished


def test_reap_finishes_what_has_stopped_and_disables_keys_nobody_owns(tmp_path: Path) -> None:
    launcher, keys, hawk = _launcher(tmp_path)
    alive = asyncio.run(launcher.start({}, 10.0, "alive"))
    # an audit that ended while the launcher was away
    hawk.live.clear()
    # a key minted elsewhere, or left by a lost record
    keys.create(f"{Records(tmp_path).prefix}orphan-9999", 2.0)
    keys.create("inv-someoneelse-live-0001", 2.0)  # another operator's running audit
    reaped = asyncio.run(launcher.reap())
    assert alive.id in reaped and any("orphan-9999" in r for r in reaped)
    others = [k for k in keys.keys.values() if k["name"] == "inv-someoneelse-live-0001"]
    assert others and not others[0]["disabled"]
    assert all(
        k["disabled"] for k in keys.keys.values() if k["name"].startswith(Records(tmp_path).prefix)
    )
    assert Records(tmp_path).load(alive.id).stage == "finished"


def test_reap_leaves_a_running_audit_alone(tmp_path: Path) -> None:
    launcher, keys, _ = _launcher(tmp_path)
    record = asyncio.run(launcher.start({}, 10.0, "busy"))
    assert asyncio.run(launcher.reap()) == []
    assert not keys.get(record.key_hash).disabled  # type: ignore[arg-type]


def test_one_audit_at_a_time(tmp_path: Path) -> None:
    """One auditor user holds one key; a second launch would share it."""
    launcher, _, _ = _launcher(tmp_path)
    first = asyncio.run(launcher.start({}, 10.0, "one"))
    with pytest.raises(RuntimeError, match=f"{first.id} is still running"):
        asyncio.run(launcher.start({}, 10.0, "two"))
    with pytest.raises(ValueError, match="positive"):
        asyncio.run(launcher.start({}, 0, "three"))


def test_secrets_come_from_a_command_else_a_warned_environment_variable(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("OPENROUTER_MANAGEMENT_KEY", raising=False)
    monkeypatch.delenv("INSPECT_AUDIT_MANAGEMENT_KEY_COMMAND", raising=False)
    with pytest.raises(
        secrets.SecretUnavailableError, match="INSPECT_AUDIT_MANAGEMENT_KEY_COMMAND"
    ):
        secrets.management_key()
    monkeypatch.setenv("OPENROUTER_MANAGEMENT_KEY", "sk-or-mgmt-env")
    assert secrets.management_key() == "sk-or-mgmt-env"
    assert "unprotected" in capsys.readouterr().err
    monkeypatch.setenv("INSPECT_AUDIT_MANAGEMENT_KEY_COMMAND", "printf sk-or-mgmt-cmd")
    assert secrets.management_key() == "sk-or-mgmt-cmd"  # the command wins
    monkeypatch.setenv("INSPECT_AUDIT_MANAGEMENT_KEY_COMMAND", "false")
    with pytest.raises(secrets.SecretUnavailableError, match="failed"):
        secrets.management_key()


def test_a_rotated_refresh_token_is_the_one_used_and_reported(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A rotating identity provider retires the old token: the job must get the new one."""
    import io
    import urllib.request
    from types import SimpleNamespace

    from inspect_audit.dispatch.hawk import HawkDelivery

    reply = {"access_token": "access", "refresh_token": "rt-new", "expires_in": 3600}
    monkeypatch.setattr(
        urllib.request, "urlopen", lambda *a, **k: io.BytesIO(json.dumps(reply).encode())
    )
    delivery = object.__new__(HawkDelivery)
    delivery.server = SimpleNamespace(client_id="client", token_endpoint="https://idp/token")  # type: ignore[assignment]
    assert delivery.auditor_tokens("rt-old") == ("access", "rt-new")
    assert "rotated" in capsys.readouterr().err
    reply.pop("refresh_token")
    assert delivery.auditor_tokens("rt-old") == ("access", "rt-old")
    assert capsys.readouterr().err == ""
