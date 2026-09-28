"""Model prices a job needs, registered with Inspect without the network."""

from typing import Any

PRICE_KEYS = ("input", "output", "input_cache_read", "input_cache_write")


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
