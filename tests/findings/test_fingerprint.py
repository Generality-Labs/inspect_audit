"""The fingerprint recipe is the identity contract; pin it."""

from hashlib import sha256

from inspect_audit.findings.fingerprint import FINGERPRINT_VERSION, fingerprint
from inspect_audit.findings.models import CodeLocation


def test_recipe_is_pinned() -> None:
    primary = CodeLocation(role="primary", file="src/inspect_evals/stereoset/stereoset.py", line=64)
    recipe = "inspect_evals_lint|IEBP008|inspect_evals/stereoset|code:src/inspect_evals/stereoset/stereoset.py:64"
    assert fingerprint("inspect_evals_lint", "IEBP008", "inspect_evals/stereoset", primary) == (
        "sha256:" + sha256(recipe.encode()).hexdigest()
    )
    assert FINGERPRINT_VERSION == 1


def test_every_component_changes_the_value() -> None:
    a = CodeLocation(file="a.py", line=1)
    base = fingerprint("p", "r", "e", a)
    assert fingerprint("q", "r", "e", a) != base
    assert fingerprint("p", "s", "e", a) != base
    assert fingerprint("p", "r", "f", a) != base
    assert fingerprint("p", "r", "e", CodeLocation(file="a.py", line=2)) != base


def test_related_fields_do_not_change_the_value() -> None:
    a = CodeLocation(file="a.py", line=1, column=3, quote="x")
    b = CodeLocation(file="a.py", line=1, column=9, quote="y", role="primary")
    assert fingerprint("p", "r", "e", a) == fingerprint("p", "r", "e", b)
