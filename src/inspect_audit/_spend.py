"""What the audit has spent: the one owner of money (FRICTION 99).

Spent is the auditor's own calls (Inspect's count for its sample) plus every job's
spend so far: the job's token counts as its backend reports them, running or finished,
priced here (`_prices.cost_of`) at the prices the audit registered when it started.
Nothing is reserved and no job carries a cap (James, 2026-11-13: "trust the agent to
reason about spend ... not prespecify stuff which causes caps"): the auditor sees what
each job has cost and decides. The operator's budget is the one stop: no launch once
what has been spent reaches it.
"""

import asyncio
import json
import os
import urllib.request
from dataclasses import dataclass
from typing import Any

from inspect_ai.model import get_model
from inspect_ai.tool import Tool, ToolError, tool
from inspect_ai.util import store_as

from ._prices import cost_of
from ._report import AuditRecord
from .dispatch import Runs
from .dispatch.hawk import HawkLogin

OPENROUTER = "https://openrouter.ai/api/v1"


def usd(amount: float) -> str:
    """Dollars as the budget shows them.

    Cents, or three places below a cent, so a cheap job never reads as free.
    """
    return f"${amount:.2f}" if amount == 0 or amount >= 0.01 else f"${amount:.3f}"


@dataclass(frozen=True)
class JobSpend:
    label: str
    kind: str
    state: str
    usd: float | None  # None: the backend could not say
    samples_done: int
    unpriced: tuple[str, ...]  # models whose tokens are not in `usd`


class Spend:
    def __init__(
        self,
        runs: Runs | None,
        budget_usd: float | None,
        hawk_api_url: str | None = None,
        openrouter: bool = False,
    ) -> None:
        self.runs = runs
        self.budget_usd = budget_usd
        # the keys whose balance `budget` reports: the Hawk user's, OPENROUTER_API_KEY
        self.hawk_api_url = hawk_api_url
        self.openrouter = openrouter

    def own(self) -> float:
        """The auditor's own calls so far."""
        from inspect_ai.util import sample_limits

        return float(sample_limits().cost.usage)

    async def jobs(self) -> list[JobSpend]:
        """Every launched job's spend so far."""
        found: list[JobSpend] = []
        for j in store_as(AuditRecord).jobs:
            run_id, state = str(j["run_id"]), "unknown"
            usd: float | None = None
            done, unpriced = 0, ()
            if self.runs is not None:
                try:
                    state = str(await self.runs.status(run_id))
                    usage, done = await self.runs.usage(run_id)
                    usd, missing = cost_of(usage)
                    unpriced = tuple(missing)
                except Exception:
                    usd = None  # a failed lookup is unknown, not free
            found.append(JobSpend(str(j["label"]), str(j["kind"]), state, usd, done, unpriced))
        return found

    async def spent(self) -> tuple[float, list[JobSpend]]:
        jobs = await self.jobs()
        return self.own() + sum(j.usd or 0.0 for j in jobs), jobs

    async def refuse_if_spent(self) -> None:
        """The one stop: refuse a launch once spend has reached the operator's budget.

        Spend that cannot be read (a failed lookup, a model with no price) is unknown, not
        free: with a budget, a launch is refused until it can be read (FRICTION 114).
        """
        if self.budget_usd is None:
            return
        total, jobs = await self.spent()
        unknown = [j.label for j in jobs if j.usd is None or j.unpriced]
        if unknown:
            raise ToolError(
                f"cannot read what {', '.join(unknown)} spent, so the ${self.budget_usd:.2f} "
                "budget cannot be checked: try again shortly, or stop those jobs"
            )
        if total >= self.budget_usd:
            raise ToolError(
                f"the ${self.budget_usd:.2f} audit budget is spent (${total:.2f} so far, "
                "your calls and every job's). Collect what is running and submit findings "
                "with the evidence you have."
            )

    async def report(self) -> list[str]:
        """The budget tool's lines: own calls, each job, the total, what is left."""
        total, jobs = await self.spent()
        lines = [f"Your own calls: {usd(self.own())}"]
        for j in jobs:
            spent = f"{usd(j.usd)} so far" if j.usd is not None else "spend unknown"
            extra = f"; no price for {', '.join(j.unpriced)}" if j.unpriced else ""
            done = f", {j.samples_done} samples done" if j.samples_done else ""
            lines.append(f"  {j.label} ({j.kind}, {j.state}): {spent}{done}{extra}")
        lines.append(f"Spent so far: {usd(total)} (running jobs count what they have spent)")
        if self.budget_usd is not None:
            left = self.budget_usd - total
            lines.append(
                f"Audit budget: ${self.budget_usd:.2f}; ${left:.2f} "
                f"({100 * left / self.budget_usd:.0f}%) left for your calls and every job"
            )
        return lines

    async def balances(self) -> list[str]:
        """What the model keys this audit bills to have left, as their owners say."""
        return await key_balance(
            self.hawk_api_url, self.openrouter and str(get_model()).startswith("openrouter/")
        )


@tool(name="budget")
def budget(spend: Spend) -> Tool:
    async def execute() -> str:
        """What this audit has spent so far, job by job, and what is left.

        Spent: your own calls and every job's spend so far, running or finished, from
        its token counts. No job is capped and nothing is reserved: decide what the
        work is worth from these costs, watch this as you go, and stop or stop jobs when
        it is not. The model key's balance, below, counts everything that bills to it.
        """
        return "\n".join([*await spend.report(), *await spend.balances()])

    return execute


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
