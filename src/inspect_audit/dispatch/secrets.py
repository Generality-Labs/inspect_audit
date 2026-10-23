"""Operator secrets, fetched by a command that prints them: never stored by us.

The pattern git credential helpers and AWS `credential_process` use. The command can be
the macOS keychain behind Touch ID, a password manager's CLI, `pass`, or anything that
prints the secret. An environment variable works for iteration and says so.
"""

import os
import shlex
import subprocess
import sys


class SecretUnavailableError(RuntimeError):
    """A secret could not be fetched; the message says how to provide it."""


def fetch(name: str, *, command_var: str, env_var: str) -> str:
    """The secret `name`: from the command in `command_var`, else `env_var`."""
    command = os.environ.get(command_var)
    if command:
        result = subprocess.run(
            shlex.split(command), capture_output=True, text=True, timeout=120, check=False
        )
        if result.returncode != 0:
            raise SecretUnavailableError(f"{name}: `{command}` failed ({result.returncode})")
        value = result.stdout.strip()
        if not value:
            raise SecretUnavailableError(f"{name}: `{command}` printed nothing")
        return value
    value = os.environ.get(env_var, "").strip()
    if value:
        print(f"warning: {name} is read from {env_var}, unprotected", file=sys.stderr)
        return value
    raise SecretUnavailableError(
        f"{name} is not available: set {command_var} to a command that prints it "
        f"(e.g. the Touch ID keychain helper), or {env_var} for iteration"
    )


def management_key() -> str:
    """OpenRouter's Management API key: it creates, reads and disables keys."""
    return fetch(
        "OpenRouter management key",
        command_var="INSPECT_AUDIT_MANAGEMENT_KEY_COMMAND",
        env_var="OPENROUTER_MANAGEMENT_KEY",
    )


def auditor_refresh_token() -> str:
    """The auditor user's Hawk refresh token, from a `hawk login` as that user."""
    return fetch(
        "auditor refresh token",
        command_var="INSPECT_AUDIT_AUDITOR_TOKEN_COMMAND",
        env_var="INSPECT_AUDIT_AUDITOR_REFRESH_TOKEN",
    )
