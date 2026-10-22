"""A store's own deployment configuration, read from the store root.

`audit.toml` names how targets are resolved and where the evals live; `pilot.yaml` holds the
per-eval scan declarations. Both are optional: without them every default applies. The package
ships no values here; a deployment keeps its own beside its data.
"""

from __future__ import annotations

import tomllib
from typing import Literal

from pydantic import BaseModel, ConfigDict, ValidationError

from .config import Config, load_config_text
from .fs import StoreFS

DEPLOYMENT_FILE = "audit.toml"
PILOT_FILE = "pilot.yaml"


class EvalsSection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    profile: Literal["inspect_evals"] = "inspect_evals"  # how targets resolve; the only one today
    root: str | None = None  # default --root for run, relative to the current directory


class Deployment(BaseModel):
    # Tables this version does not know are kept, not refused: a store is shared, and a newer
    # CLI's audit.toml must not stop an older one. Typos inside a known table are still errors.
    model_config = ConfigDict(extra="allow")
    evals: EvalsSection = EvalsSection()


def unknown_tables(deployment: Deployment) -> list[str]:
    """Top-level names in audit.toml this version ignores, sorted, for a warning."""
    return sorted(deployment.model_extra or {})


def load_deployment(fs: StoreFS) -> Deployment:
    """The store's `audit.toml`, or every default when it is absent. Errors name the file."""
    if not fs.is_file(DEPLOYMENT_FILE):
        return Deployment()
    try:
        return Deployment.model_validate(tomllib.loads(fs.read_text(DEPLOYMENT_FILE)))
    except (tomllib.TOMLDecodeError, ValidationError) as ex:
        raise ValueError(f"{DEPLOYMENT_FILE} under {fs.locator}: {ex}") from ex


def store_pilot(fs: StoreFS) -> Config | None:
    """The store's `pilot.yaml`, or None when it has none. Errors name the file."""
    if not fs.is_file(PILOT_FILE):
        return None
    return load_config_text(fs.read_text(PILOT_FILE), f"{PILOT_FILE} under {fs.locator}")
