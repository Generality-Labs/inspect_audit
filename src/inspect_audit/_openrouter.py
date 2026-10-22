"""Optional OpenRouter pricing adapter; model identities remain provider qualified."""

import json
import urllib.request

from ._prices import valid_price

OPENROUTER = "https://openrouter.ai/api/v1"


def openrouter_prices(models: list[str]) -> dict[str, dict[str, float]]:
    """OpenRouter's published per-million prices, by the name Inspect runs each under.

    An audit job caps each item's spend with Inspect's cost limit, which needs a
    price for the auditor; the job registers these itself.
    """
    request = urllib.request.Request(
        f"{OPENROUTER}/models", headers={"User-Agent": "inspect_audit"}
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        listing = {str(m["id"]): m.get("pricing") or {} for m in json.load(response)["data"]}
    prices: dict[str, dict[str, float]] = {}
    for model in models:
        pricing = listing.get(model.removeprefix("openrouter/"))
        if pricing is None:
            raise ValueError(f"OpenRouter does not list {model!r}")
        price = {
            key: float(pricing.get(field) or 0) * 1_000_000
            for key, field in (
                ("input", "prompt"),
                ("output", "completion"),
                ("input_cache_read", "input_cache_read"),
                ("input_cache_write", "input_cache_write"),
            )
        }
        if not valid_price(price):
            raise ValueError(f"OpenRouter lists no usable price for {model!r}")
        prices[model] = price
    return prices
