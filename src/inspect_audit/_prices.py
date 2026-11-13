"""Model prices from one table: what Hawk's proxy bills (`GET /usage/prices`).

The audit, which holds a Hawk login, looks up a price for every model it may use when it
starts and refuses any it cannot price (`price_models`; James, 2026-11-13: "just don't
run if you can't find the model"). Those prices are the one pricing: the auditor's own
`cost_limit` (its budget), and every job's spend, computed from the job's token counts
(`cost_of`, used by `_spend`). Children get no prices and carry no caps (FRICTION 99,
100). Off Hawk (no `HAWK_API_URL`) Inspect's built-in prices are all there is.

Temporary (FRICTION 84): Hawk PR #1542 fills `model_cost_config` from this same table,
and Inspect's `set_model_cost` refuses models it does not know, hence `set_model_info`
here.
"""

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
# a mock model costs nothing, and its price says so
MOCK = "mockllm/"
FREE = dict.fromkeys(FIELDS, 0.0)


class UnpricedModelError(ValueError):
    """A model Hawk has no usable price for: it is refused rather than run unmetered."""


def prices_for(models: list[str]) -> dict[str, dict[str, float]]:
    """Hawk's price for each model, keyed by the name the job calls it by.

    Needs a Hawk login (the operator's, or a user-mode runner's); off Hawk, nothing.
    Raises `UnpricedModelError` naming every model Hawk does not price. A mock model
    costs nothing, and says so: a job run under one (a rescore of a grader that calls no
    model) still needs a price for its `cost_limit` (smoke round 7, FRICTION 96).
    """
    mocks = {m: FREE for m in models if m.startswith(MOCK)}
    api_url = os.environ.get("HAWK_API_URL")
    if not api_url:
        return mocks
    from hawk.core.providers import registry_model_name

    table = _hawk_table(api_url)
    prices: dict[str, dict[str, float]] = dict(mocks)
    missing: list[str] = []
    for model in dict.fromkeys(models):
        if model.startswith(MOCK):
            continue
        row = table.get(registry_model_name(model, strict=False))
        price = {k: (row or {}).get(col) or 0.0 for k, col in FIELDS.items()}
        if row is None or not usable(price):
            missing.append(model)
        else:
            prices[model] = price
    if missing:
        raise UnpricedModelError(
            f"Hawk has no usable price for {', '.join(missing)}: write each model as Hawk "
            "serves it (an OpenRouter model is openai/openrouter/<org>/<model>)"
        )
    return prices


def usable(price: dict[str, Any]) -> bool:
    """Whether a cost limit can rely on this price.

    Every field must be a finite non-negative number (routers list -1 for "depends on
    the route"), with an input or output rate (all zeros would read every call as free).
    """
    values = [price.get(k, 0.0) for k in FIELDS]
    return all(
        isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) and v >= 0
        for v in values
    ) and bool(price.get("input") or price.get("output"))


def price_models(models: list[str]) -> None:
    """Look up and register prices for this process's own models (needs a Hawk login)."""
    register_prices(prices_for(models))


def register_prices(prices: dict[str, dict[str, Any]] | None) -> None:
    """Register prices handed over by the job's launcher."""
    for model, price in (prices or {}).items():
        register_price(model, price)


def register_price(model: str, price: dict[str, Any]) -> None:
    """Give Inspect a price for `model`, creating its entry if Inspect has none.

    An unusable price (see `usable`) is refused, as `prices_for` refuses it.
    """
    from inspect_ai.model import ModelCost, ModelInfo, get_model_info, set_model_info

    if not usable(price) and not (model.startswith(MOCK) and price == FREE):
        raise UnpricedModelError(f"unusable price for {model}: {price}")
    values = [price.get(k, 0.0) for k in FIELDS]
    cost = ModelCost(**{k: float(v) for k, v in zip(FIELDS, values, strict=True)})
    set_model_info(model, (get_model_info(model) or ModelInfo()).model_copy(update={"cost": cost}))


@functools.cache
def _hawk_table(api_url: str) -> dict[str, dict[str, Any]]:
    """Hawk's price rows by public model name, fetched once per process.

    Called while a task loads, which may be inside a running event loop.
    """
    from ._sync import run
    from .dispatch.hawk import prices

    rows = run(prices(api_url))
    return {str(r["model"]): r for r in rows if r.get("model")}


def cost_of(usage: dict[str, Any]) -> tuple[float, list[str]]:
    """What token counts cost at the prices registered here, and the models unpriced.

    Unpriced models' tokens are not counted in the total.

    The audit registers a price for every model it may use when it starts (`price_models`),
    so this prices any job it launched, on any backend, from the job's token counts.
    """
    from inspect_ai.model import ModelUsage, compute_model_cost, get_model_info

    total = 0.0
    unpriced: list[str] = []
    for model, counts in usage.items():
        info = get_model_info(model)
        if info is None or info.cost is None:
            unpriced.append(model)
            continue
        tokens = counts if isinstance(counts, ModelUsage) else ModelUsage.model_validate(counts)
        total += compute_model_cost(info.cost, tokens)
    return total, sorted(unpriced)
