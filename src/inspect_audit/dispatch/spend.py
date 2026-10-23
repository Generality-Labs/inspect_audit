"""What the model keys a run bills to have left, as their owners report it."""

import asyncio
import json
import os
import urllib.request
from typing import Any

from .hawk import HawkLogin

OPENROUTER = "https://openrouter.ai/api/v1"


async def key_balance(hawk_api_url: str | None, openrouter: bool) -> list[str]:
    """What the model keys this audit spends on have left, as their owners say.

    Calls made as a Hawk user (the auditor in a Hawk pod, jobs run on Hawk)
    bill to that user's key, which Hawk reports. Calls made on OPENROUTER_API_KEY
    (the auditor in a local box, local jobs) bill to that key, which OpenRouter
    reports. Which applies is declared by the caller; nothing is computed here.
    """

    def get(url: str, token: str) -> dict[str, Any]:
        request = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
        with urllib.request.urlopen(request, timeout=30) as response:
            return dict(json.load(response))

    lines: list[str] = []
    if hawk_api_url:
        try:
            login = HawkLogin(hawk_api_url)
            body = await asyncio.to_thread(
                get, f"{hawk_api_url.rstrip('/')}/usage/balance", await login.access_token()
            )
            remaining = body.get("remaining_usd")
            if body.get("status") == "shared":
                lines.append("Hawk: this user has no capped key; spending is not limited by a key")
            elif isinstance(remaining, (int, float)):
                lines.append(f"Hawk key: ${float(remaining):.2f} remaining")
            else:
                lines.append(f"Hawk key: remaining unknown (Hawk answered {body})")
        except Exception as ex:
            lines.append(f"Hawk key: could not ask ({type(ex).__name__}: {ex})")
    if openrouter:
        try:
            key = os.environ["OPENROUTER_API_KEY"]
            data = (await asyncio.to_thread(get, f"{OPENROUTER}/key", key)).get("data") or {}
            if data.get("limit") is None:
                lines.append(f"OpenRouter key: no limit; ${float(data.get('usage') or 0):.2f} used")
            elif isinstance(data.get("limit_remaining"), (int, float)):
                lines.append(
                    f"OpenRouter key: ${float(data['limit_remaining']):.2f} of "
                    f"${float(data['limit']):.2f} remaining"
                )
            else:
                lines.append(f"OpenRouter key: remaining unknown (OpenRouter answered {data})")
        except Exception as ex:
            lines.append(f"OpenRouter key: could not ask ({type(ex).__name__}: {ex})")
    return lines or ["no model key declared to ask about"]
