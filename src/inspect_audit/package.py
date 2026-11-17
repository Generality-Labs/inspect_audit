"""Audit packages: what an audit rates against and how it is written up.

The machinery (the auditor, its jobs, the investigators, the register, the budget) is
the same for every audit. A package supplies the two parts that differ between audit
styles:

- a `Rubric`, which the auditor gets from the start: the questions it answers, the
  skill that says how to rate them, and the check that a submission rates them all;
- one or more `Publisher` formats, revealed after findings are submitted: the
  writing skill, the template and the build.

Packages register under the `inspect_audit.packages` entry point group, so one named
in an audit config is found by name wherever it is installed:

    [project.entry-points."inspect_audit.packages"]
    epoch = "inspect_audit_epoch:package"

`gl`, the Generality Labs framework, is one: it ships with inspect_audit and is
registered the same way. A package imports from inspect_audit only through this module
(tests/test_layering.py holds GL to that), so it can live in its own distribution.
"""

import sys
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from importlib.metadata import EntryPoint, entry_points
from pathlib import Path

from inspect_ai.tool import Tool

# what a package works with: the submitted bundle and the box it renders in
from ._findings import (
    Finding,
    Findings,
    FindingsState,
    Rating,
    Review,
    encoded,
    read_bundle,
    restore_bundle,
)
from ._render import box_has, ensure_browser, render_in_box, reported, tree
from ._verdicts import Evidence

GROUP = "inspect_audit.packages"
DEFAULT = "gl"


@dataclass(frozen=True)
class Rubric:
    """What the auditor rates the benchmark against, given to it from the start.

    `stage(directory)` writes the rubric's data into the auditor's workspace
    (/workspace/analysis/framework). `skill` is the directory of the skill that says
    how to rate: the auditor and its investigators load it. `guide` is the paragraph
    of the auditor's instructions that says what a submission must rate, naming that
    skill. `validate` refuses a submission whose ratings and findings the rubric
    cannot read, whatever format it is published in.
    """

    skill: Path
    stage: Callable[[Path], None]
    guide: str
    validate: Callable[[Findings], None] = lambda findings: None


@dataclass(frozen=True)
class Publisher:
    """A trusted publishing format: skills, optional tools and an output builder.

    ``build(root, bundle)`` reads draft output from the sandbox and returns a host
    directory of finished artifacts. The second argument is the trusted submitted
    bundle, independent of the workspace. It must not edit the submitted bundle. No
    benchmark installation is required.
    """

    skills: list[str]
    build: Callable[[Path, Path], Awaitable[Path]]
    tools: Callable[[Path], list[Tool]] = lambda root: []
    validate: Callable[[Findings], None] = lambda findings: None
    # checked in setup, before the first model call: a missing renderer ends the audit then,
    # not after hours of work (CVE-Bench Hard froze 9 findings, then could not publish)
    preflight: Callable[[], Awaitable[None]] | None = None


@dataclass(frozen=True)
class AuditPackage:
    """A rubric and the formats its findings can be published in; the first is the default."""

    rubric: Rubric
    formats: dict[str, Callable[[], Publisher]]
    # how a child job installs this package, for the investigators that load its
    # rubric skill: a pip requirement, or None when it ships with inspect_audit
    requirement: str | None = None
    # former names of formats, still accepted in configs
    aliases: dict[str, str] = field(default_factory=dict)

    def publisher(self, name: str | None = None) -> tuple[str, Publisher]:
        """The named format (default: the first), with its canonical name."""
        chosen = self.aliases.get(name, name) if name else next(iter(self.formats))
        if chosen not in self.formats:
            raise ValueError(
                f"publisher {name!r} is not a format of this package: {', '.join(self.formats)}"
            )
        return chosen, self.formats[chosen]()

    def validate(self, publisher: Publisher) -> Callable[[Findings], None]:
        """The submission check: the rubric's, then the format's."""

        def check(findings: Findings) -> None:
            self.rubric.validate(findings)
            publisher.validate(findings)

        return check


def installed_requirement(distribution: str) -> str | None:
    """How a child job installs `distribution` as it is installed here.

    For a package's `requirement`: a git install at its commit, or a local checkout's
    pushed commit; None when neither can be told.
    """
    from .dispatch.packages import distribution_spec

    return distribution_spec(distribution)


def installed() -> dict[str, EntryPoint]:
    """Every installed package, by name."""
    return {e.name: e for e in entry_points(group=GROUP)}


def load_package(name: str | None = None) -> AuditPackage:
    """The installed package registered under `name` (default: gl)."""
    name = name or DEFAULT
    found = installed()
    if name not in found:
        raise ValueError(
            f"No audit package {name!r} is installed; installed: {', '.join(sorted(found)) or 'none'}"
        )
    package = found[name].load()()
    if not isinstance(package, AuditPackage):
        raise TypeError(f"{found[name].value} must return inspect_audit.package.AuditPackage")
    return package


@dataclass(frozen=True)
class Selection:
    """The package and format one audit or publishing run uses."""

    name: str
    package: AuditPackage
    format: str
    publisher: Publisher

    @property
    def validate(self) -> Callable[[Findings], None]:
        return self.package.validate(self.publisher)


def select(package: str | None, publisher: str | None) -> Selection:
    """Resolve an audit config's `package` and `publisher` (a format of that package).

    A `module:factory` publisher, the older way to plug one in, still works: it
    publishes under the named package's rubric.
    """
    name = package or DEFAULT
    loaded = load_package(name)
    if publisher and ":" in publisher:
        import importlib

        module, _, factory = publisher.partition(":")
        result = getattr(importlib.import_module(module), factory)()
        if not isinstance(result, Publisher):
            raise TypeError("Publisher factories must return inspect_audit.package.Publisher")
        return Selection(name, loaded, publisher, result)
    chosen, built = loaded.publisher(publisher)
    return Selection(name, loaded, chosen, built)


def main() -> None:
    """List the installed audit packages and their formats."""
    for name, entry in sorted(installed().items()):
        try:
            package = load_package(name)
        except Exception as ex:  # a broken package is listed, not hidden
            print(f"{name}\t{entry.value}\tfailed to load: {ex}", file=sys.stdout)
            continue
        formats = ", ".join(package.formats)
        print(f"{name}\t{entry.value}\tformats: {formats}\trubric: {package.rubric.skill.name}")


__all__ = [
    "DEFAULT",
    "GROUP",
    "AuditPackage",
    "Evidence",
    "Finding",
    "Findings",
    "FindingsState",
    "Publisher",
    "Rating",
    "Review",
    "Rubric",
    "Selection",
    "box_has",
    "encoded",
    "ensure_browser",
    "installed",
    "installed_requirement",
    "load_package",
    "read_bundle",
    "render_in_box",
    "reported",
    "restore_bundle",
    "select",
    "tree",
]
