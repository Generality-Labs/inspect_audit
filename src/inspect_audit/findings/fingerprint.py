"""The identity recipe. Computed once at write time and stored; never recomputed on read."""

from hashlib import sha256

from .models import Location

FINGERPRINT_VERSION = 1


def fingerprint(producer: str, rule: str, eval: str, primary: Location) -> str:
    """Stable identity of a finding across runs: producer, rule, eval and primary location key."""
    recipe = f"{producer}|{rule}|{eval}|{primary.key()}"
    return "sha256:" + sha256(recipe.encode()).hexdigest()
