"""What the audit has spent: the one owner of money (FRICTION 99).

Spent is the auditor's own calls (Inspect's count for its sample) plus every job's
spend so far: the job's token counts as its backend reports them, running or finished,
priced here (`_prices.cost_of`) at the prices the audit registered when it started.
Nothing is reserved and no job carries a cap (James, 2026-11-13: "trust the agent to
reason about spend ... not prespecify stuff which causes caps"): the auditor sees what
each job has cost and decides. The operator's budget is the one stop: no launch once
what has been spent reaches it.
"""

from dataclasses import dataclass

from inspect_ai.tool import ToolError
from inspect_ai.util import store_as

from ._prices import cost_of
from ._report import AuditRecord
from .dispatch import Runs


@dataclass(frozen=True)
class JobSpend:
    label: str
    kind: str
    state: str
    usd: float | None  # None: the backend could not say
    samples_done: int
    unpriced: tuple[str, ...]  # models whose tokens are not in `usd`


class Spend:
    def __init__(self, runs: Runs | None, budget_usd: float | None) -> None:
        self.runs = runs
        self.budget_usd = budget_usd

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
        """The one stop: refuse a launch once spend has reached the operator's budget."""
        if self.budget_usd is None:
            return
        total, _ = await self.spent()
        if total >= self.budget_usd:
            raise ToolError(
                f"the ${self.budget_usd:.2f} audit budget is spent (${total:.2f} so far, "
                "your calls and every job's). Collect what is running and submit findings "
                "with the evidence you have."
            )

    async def report(self) -> list[str]:
        """The budget tool's lines: own calls, each job, the total, what is left."""
        total, jobs = await self.spent()
        lines = [f"Your own calls: ${self.own():.2f}"]
        for j in jobs:
            usd = f"${j.usd:.2f} so far" if j.usd is not None else "spend unknown"
            extra = f"; no price for {', '.join(j.unpriced)}" if j.unpriced else ""
            done = f", {j.samples_done} samples done" if j.samples_done else ""
            lines.append(f"  {j.label} ({j.kind}, {j.state}): {usd}{done}{extra}")
        lines.append(f"Spent so far: ${total:.2f} (running jobs count what they have spent)")
        if self.budget_usd is not None:
            left = self.budget_usd - total
            lines.append(
                f"Audit budget: ${self.budget_usd:.2f}; ${left:.2f} "
                f"({100 * left / self.budget_usd:.0f}%) left for your calls and every job"
            )
        return lines
