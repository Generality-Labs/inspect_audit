"""OpenRouter's key management API: create a capped key, read it, disable it.

https://openrouter.ai/docs/features/provisioning-api-keys. Authenticated with a
Management key, which can manage keys but cannot call models.
"""

import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any

API = "https://openrouter.ai/api/v1/keys"


@dataclass(frozen=True)
class KeyState:
    hash: str
    name: str
    limit: float | None
    remaining: float | None
    usage: float
    disabled: bool


class OpenRouterKeys:
    def __init__(self, management_key: str, api: str = API) -> None:
        self._auth = {"Authorization": f"Bearer {management_key}"}
        self.api = api

    def _call(self, method: str, path: str = "", body: dict[str, Any] | None = None) -> Any:
        data = json.dumps(body).encode() if body is not None else None
        headers = {**self._auth, **({"Content-Type": "application/json"} if data else {})}
        request = urllib.request.Request(self.api + path, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                return json.load(response)
        except urllib.error.HTTPError as ex:
            # the body says why; the key never appears in it
            raise RuntimeError(
                f"OpenRouter {method} {path or '/'}: {ex.code} {ex.read()[:300]!r}"
            ) from ex

    def create(self, name: str, limit: float) -> tuple[str, str]:
        """A new key capped at `limit` dollars: (its hash, its secret, shown only now)."""
        reply = self._call("POST", body={"name": name, "limit": limit})
        data = reply.get("data") or {}
        return str(data["hash"]), str(reply["key"])

    def get(self, key_hash: str) -> KeyState:
        return _state((self._call("GET", f"/{key_hash}").get("data")) or {})

    def list(self, prefix: str) -> list[KeyState]:
        """Every key whose name starts with `prefix`, across pages."""
        found: list[KeyState] = []
        offset = 0
        while True:
            page = self._call("GET", f"?offset={offset}").get("data") or []
            found += [_state(k) for k in page if str(k.get("name", "")).startswith(prefix)]
            if len(page) < 100:
                return found
            offset += len(page)

    def disable(self, key_hash: str) -> None:
        self._call("PATCH", f"/{key_hash}", {"disabled": True})


def _state(data: dict[str, Any]) -> KeyState:
    return KeyState(
        hash=str(data.get("hash", "")),
        name=str(data.get("name", "")),
        limit=data.get("limit"),
        remaining=data.get("limit_remaining"),
        usage=float(data.get("usage") or 0.0),
        disabled=bool(data.get("disabled")),
    )
