"""`inspect-audit-launch`: start, check, finish and reap capped audits on Hawk.

  inspect-audit-launch start audit.eval-set.yaml --cap 20 --label simpleqa
  inspect-audit-launch status inv-simpleqa-1a2b3c4d
  inspect-audit-launch finish inv-simpleqa-1a2b3c4d
  inspect-audit-launch reap

Secrets come from commands that print them (see `secrets.py`):
INSPECT_AUDIT_MANAGEMENT_KEY_COMMAND for OpenRouter's management key, and
INSPECT_AUDIT_AUDITOR_TOKEN_COMMAND for the auditor user's refresh token.
The operator's own `hawk login` installs the key, and must be a Hawk admin.
"""

import argparse
import asyncio
import json
import sys
from pathlib import Path

import yaml

from . import secrets
from .hawk import HawkDelivery
from .launcher import Launcher, Records
from .openrouter import OpenRouterKeys


def launcher() -> Launcher:
    return Launcher(
        keys=OpenRouterKeys(secrets.management_key()),
        delivery=HawkDelivery(),
        records=Records(),
        refresh_token=secrets.auditor_refresh_token,
    )


async def run(args: argparse.Namespace) -> object:
    launch = launcher()
    if args.command == "start":
        config = yaml.safe_load(Path(args.config).read_text())
        record = await launch.start(config, args.cap, args.label)
        return {"started": record.id, "cap_usd": record.cap_usd}
    if args.command == "status":
        return await launch.status(args.audit)
    if args.command == "finish":
        record = await launch.finish(args.audit)
        return {"finished": record.id, "spent_usd": record.usage_usd}
    return {"reaped": await launch.reap()}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="inspect-audit-launch", description=(__doc__ or "").splitlines()[0]
    )
    commands = parser.add_subparsers(dest="command", required=True)
    start = commands.add_parser("start", help="mint a capped key and launch an audit")
    start.add_argument("config", help="the audit's Hawk eval-set config (YAML)")
    start.add_argument("--cap", type=float, required=True, help="dollars the key may spend")
    start.add_argument("--label", required=True, help="a short name, part of the key's name")
    commands.add_parser("status", help="an audit and its key").add_argument("audit")
    commands.add_parser("finish", help="record spend and disable the key").add_argument("audit")
    commands.add_parser("reap", help="revoke keys of audits no longer running")
    args = parser.parse_args(argv)
    try:
        print(json.dumps(asyncio.run(run(args)), indent=1))
    except (secrets.SecretUnavailableError, RuntimeError, ValueError) as ex:
        print(f"error: {ex}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
