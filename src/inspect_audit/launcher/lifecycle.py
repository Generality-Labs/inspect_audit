"""Start, check, finish and reap investigations: a key per investigation, always revoked.

Each investigation has one record on this machine, written before its key exists and
updated after every step, so a crash at any point leaves enough to clean up. `finish`
records what the key spent and disables it, and can be repeated. `reap` finishes every
investigation that is no longer running, and disables any `inv-` key OpenRouter holds
that no live investigation owns. The investigator user's key assignment is never
removed: removing it would hand that user the shared keys, so a disabled key stays
installed until the next investigation replaces it.
"""

import json
import secrets as random
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, Protocol

from .openrouter import KeyState

PREFIX = "inv-"
STATE_DIR = Path.home() / ".inspect-audit" / "launcher"

Stage = Literal["planned", "minted", "installed", "running", "finished"]


def now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


@dataclass
class Record:
    id: str
    cap_usd: float
    stage: Stage = "planned"
    key_hash: str | None = None
    usage_usd: float | None = None
    created: str = field(default_factory=now)
    finished: str | None = None
    note: str = ""


class Keys(Protocol):
    def create(self, name: str, limit: float) -> tuple[str, str]: ...
    def get(self, key_hash: str) -> KeyState: ...
    def list(self, prefix: str) -> list[KeyState]: ...
    def disable(self, key_hash: str) -> None: ...


class Delivery(Protocol):
    async def operator_token(self) -> str: ...
    def install(self, operator_token: str, key: str) -> None: ...
    async def submit(self, config: dict[str, Any], refresh_token: str) -> str: ...
    async def running(self, eval_set_id: str, operator_token: str) -> bool: ...


class Records:
    """One JSON file per investigation; written whole, atomically, after every step."""

    def __init__(self, root: Path = STATE_DIR) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    def save(self, record: Record) -> None:
        path = self.root / f"{record.id}.json"
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(asdict(record), indent=1))
        temporary.replace(path)

    def load(self, investigation: str) -> Record:
        return Record(**json.loads((self.root / f"{investigation}.json").read_text()))

    def all(self) -> list[Record]:
        return [
            Record(**json.loads(p.read_text())) for p in sorted(self.root.glob(f"{PREFIX}*.json"))
        ]


class Launcher:
    def __init__(
        self,
        keys: Keys,
        delivery: Delivery,
        records: Records,
        refresh_token: Callable[[], str],
    ) -> None:
        self.keys, self.delivery, self.records = keys, delivery, records
        self.refresh_token = refresh_token

    def active(self) -> list[Record]:
        return [r for r in self.records.all() if r.stage != "finished"]

    async def start(self, config: dict[str, Any], cap_usd: float, label: str) -> Record:
        """Mint, install, submit. Any failure after minting revokes the key at once."""
        if cap_usd <= 0:
            raise ValueError("the cap is a positive number of dollars")
        await self.reap()
        # one investigator user holds one key: a second investigation would share it
        if busy := self.active():
            raise RuntimeError(f"investigation {busy[0].id} is still running; finish it first")
        record = Record(id=f"{PREFIX}{label}-{random.token_hex(4)}", cap_usd=cap_usd)
        self.records.save(record)  # the plan exists before the key does
        try:
            record.key_hash, key = self.keys.create(record.id, cap_usd)
            record.stage = "minted"
            self.records.save(record)
            self.delivery.install(await self.delivery.operator_token(), key)
            del key
            record.stage = "installed"
            self.records.save(record)
            submitted = await self.delivery.submit(
                {**config, "eval_set_id": record.id}, self.refresh_token()
            )
            if submitted != record.id:
                record.note = f"Hawk named it {submitted}"
            record.stage = "running"
            self.records.save(record)
        except BaseException:
            await self.finish(record.id, reason="launch failed")
            raise
        return record

    async def status(self, investigation: str) -> dict[str, Any]:
        record = self.records.load(investigation)
        key = self.keys.get(record.key_hash) if record.key_hash else None
        return {
            **asdict(record),
            "remaining_usd": key.remaining if key else None,
            "spent_usd": key.usage if key else record.usage_usd,
        }

    async def finish(self, investigation: str, reason: str = "finished") -> Record:
        """Record the key's spend and disable it. Safe to repeat, and after a crash."""
        record = self.records.load(investigation)
        if record.key_hash:
            state = self.keys.get(record.key_hash)
            record.usage_usd = state.usage
            if not state.disabled:
                self.keys.disable(record.key_hash)
        else:
            # a crash between asking OpenRouter for a key and writing its hash down
            for state in self.keys.list(record.id):
                if state.name == record.id:
                    record.key_hash, record.usage_usd = state.hash, state.usage
                    if not state.disabled:
                        self.keys.disable(state.hash)
        record.stage = "finished"
        record.finished = record.finished or now()
        record.note = (record.note + f"; {reason}").strip("; ")
        self.records.save(record)
        return record

    async def reap(self) -> list[str]:
        """Finish what is not running; disable every inv- key no live investigation owns."""
        reaped: list[str] = []
        token: str | None = None

        async def operator() -> str:
            nonlocal token
            token = token or await self.delivery.operator_token()
            return token

        live: set[str] = set()
        for record in self.active():
            submitted = record.stage == "running"
            if submitted and await self.delivery.running(record.id, await operator()):
                live.add(record.id)
                continue
            # never submitted: its start either failed (and finished it) or died midway
            await self.finish(record.id, reason="reaped")
            reaped.append(record.id)
        for state in self.keys.list(PREFIX):
            if state.name not in live and not state.disabled:
                self.keys.disable(state.hash)
                reaped.append(f"{state.name} (key had no live investigation)")
        return reaped


Runner = Callable[..., Awaitable[Any]]
