"""Model prices from one table: what Hawk's proxy bills (`GET /usage/prices`).

Inspect enforces a `cost_limit` and reports cost only for models it has a price for.
Whoever holds a Hawk login looks prices up (`prices_for`): the audit for its own model
and for each child job's models, which it hands the child as a task argument. A child
only registers what it is given (`register_prices`): it runs with restricted
credentials, which Hawk's API does not accept. A model Hawk does not price is left
alone, so Inspect refuses its `cost_limit` with its own error. Off Hawk (no
`HAWK_API_URL`) Inspect's built-in prices are all there is.

Temporary (FRICTION 84): Hawk PR #1542 fills `model_cost_config` from this same table,
and Inspect's `set_model_cost` refuses models it does not know, hence `set_model_info`
here. Delete this module when both are fixed upstream.
"""

import asyncio
import concurrent.futures
import functools
import math
import os
from typing import Any

# Inspect's ModelCost field -> Hawk's per-million column
FIELDS = {
    "input": "input_cost_per_1m",
    "output": "output_cost_per_1m",
    "input_cache_read": "cache_read_input_cost_per_1m",
    "input_cache_write": "cache_write_input_cost_per_1m",
}


def prices_for(models: list[str]) -> dict[str, dict[str, float]]:
    """Hawk's price for each model it prices, keyed by the name the job calls it by.

    Needs a Hawk login (the operator's, or a user-mode runner's); off Hawk, nothing.
    """
    api_url = os.environ.get("HAWK_API_URL")
    if not api_url:
        return {}
    from hawk.core.providers import registry_model_name

    table = _hawk_table(api_url)
    prices: dict[str, dict[str, float]] = {}
    for model in dict.fromkeys(models):
        row = table.get(registry_model_name(model, strict=False)) or table.get(model)
        if row is not None:
            prices[model] = {k: row.get(col) or 0.0 for k, col in FIELDS.items()}
    return prices


def price_models(models: list[str]) -> None:
    """Look up and register prices for this process's own models (needs a Hawk login)."""
    register_prices(prices_for(models))


def register_prices(prices: dict[str, dict[str, Any]] | None) -> None:
    """Register prices handed over by the job's launcher."""
    for model, price in (prices or {}).items():
        register_price(model, price)


def register_price(model: str, price: dict[str, Any]) -> None:
    """Give Inspect a price for `model`, creating its entry if Inspect has none.

    A price with a negative or non-numeric field (routers list -1 for "depends on the
    route"), or with neither an input nor an output rate, is skipped: it would let a
    cost limit read every call as free.
    """
    from inspect_ai.model import ModelCost, ModelInfo, get_model_info, set_model_info

    values = [price.get(k, 0.0) for k in FIELDS]
    if any(
        not isinstance(v, (int, float)) or isinstance(v, bool) or not math.isfinite(v) or v < 0
        for v in values
    ) or not (price.get("input") or price.get("output")):
        return
    cost = ModelCost(**{k: float(v) for k, v in zip(FIELDS, values, strict=True)})
    set_model_info(model, (get_model_info(model) or ModelInfo()).model_copy(update={"cost": cost}))


@functools.cache
def _hawk_table(api_url: str) -> dict[str, dict[str, Any]]:
    """Hawk's price rows by public model name, fetched once per process.

    Called while a task loads, which may be inside a running event loop.
    """
    from .dispatch.hawk import prices

    with concurrent.futures.ThreadPoolExecutor(1) as pool:
        rows = pool.submit(asyncio.run, prices(api_url)).result()
    return {str(r["model"]): r for r in rows if r.get("model")}
