"""Model prices a job needs: Inspect's own costs, else OpenRouter's published prices."""

import json
import urllib.request
from typing import Any

PRICE_KEYS = ("input", "output", "input_cache_read", "input_cache_write")
OPENROUTER = "https://openrouter.ai/api/v1"


def prices_for_models(models: list[str]) -> dict[str, dict[str, float]]:
    """Use Inspect's configured costs; query OpenRouter only for its own routes."""
    from inspect_ai.model import get_model_info

    prices: dict[str, dict[str, float]] = {}
    router: list[str] = []
    for model in dict.fromkeys(models):
        info = get_model_info(model)
        if info is not None and info.cost is not None:
            prices[model] = info.cost.model_dump()
        elif model.startswith("openrouter/"):
            router.append(model)
        else:
            raise ValueError(
                f"No Inspect cost data for {model!r}; declare model_prices or configure Inspect model costs"
            )
    if router:
        prices.update(openrouter_prices(router))
    return prices


def valid_price(price: Any) -> bool:
    """A usable per-million price: every field a non-negative number, and input or output set.

    Routers list -1 for "depends on the route", and a price with no input or output
    rate would let a cost limit read every call as free.
    """
    if not isinstance(price, dict):
        return False
    values = [price.get(k, 0) for k in PRICE_KEYS]
    if any(not isinstance(v, (int, float)) or isinstance(v, bool) or v < 0 for v in values):
        return False
    return bool(price.get("input") or price.get("output"))


def register_prices(table: dict[str, dict[str, float]]) -> list[str]:
    """Give Inspect an entry and a price for each model, before anything prices it.

    Hawk applies a job's `model_cost_config` with `set_model_cost`, which refuses a
    model Inspect has no entry for; `set_model_info` creates the entry. Returns the
    names registered.
    """
    from inspect_ai.model import ModelCost, ModelInfo, get_model_info, set_model_info

    registered: list[str] = []
    for name, price in table.items():
        if not valid_price(price):
            continue
        cost = ModelCost(**{k: float(price.get(k, 0.0)) for k in PRICE_KEYS})
        info = (get_model_info(name) or ModelInfo()).model_copy(update={"cost": cost})
        set_model_info(name, info)
        registered.append(name)
    return registered


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
