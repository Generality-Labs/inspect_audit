"""Audit taxonomies as versioned data.

A finding names the taxonomy its `dimension` and `check` come from (`gl-audit@1`), so the
list of dimensions is not baked into the schema and two taxonomies can coexist while one
replaces the other. `gl-audit@1` is the nine-dimension framework in the LaTeX class and a
test asserts the two stay equal; `gl-audit@2` is the strategy document's proposal and is a
draft until that class follows it. Mappings between versions are data too.
"""

from __future__ import annotations

import json
from functools import cache
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

TAXONOMY_DIR = Path(__file__).parent / "taxonomies"

TAXONOMIES: dict[str, str] = {
    "gl-audit@1": "gl-audit-1.json",
    "gl-audit@2": "gl-audit-2.json",
}
DEFAULT_TAXONOMY = "gl-audit@1"

_MAPPINGS: dict[tuple[str, str], str] = {
    ("gl-audit@1", "gl-audit@2"): "gl-audit-1-to-2.json",
}


class Contribution(BaseModel):
    """A check within a dimension: `C.1` in v1, `implementation.task_specification` in v2."""

    model_config = ConfigDict(extra="forbid")
    id: str
    name: str
    definition: str | None = None
    items: list[str] = Field(default_factory=list)


class TaxonomyDimension(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    name: str
    code: str | None = None
    question: str | None = None
    validity: Literal["measurement", "evaluation"] | None = None
    contributions: list[Contribution] = Field(default_factory=list)


class Taxonomy(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    version: int
    status: Literal["pinned", "draft"]
    source: str
    dimensions: list[TaxonomyDimension]

    @property
    def ref(self) -> str:
        return f"{self.id}@{self.version}"

    def dimension_ids(self) -> set[str]:
        return {dimension.id for dimension in self.dimensions}

    def check_ids(self) -> set[str]:
        return {
            contribution.id
            for dimension in self.dimensions
            for contribution in dimension.contributions
        }


class TaxonomyMapping(BaseModel):
    """How one taxonomy's ids read in another; every source id must be covered."""

    model_config = ConfigDict(extra="forbid")
    source: str
    target: str
    dimensions: dict[str, str]
    checks: dict[str, str]


@cache
def load_taxonomy(ref: str) -> Taxonomy:
    """The taxonomy named `id@version`, from its data file."""
    name = TAXONOMIES.get(ref)
    if name is None:
        raise ValueError(f"unknown taxonomy {ref!r}; known: {', '.join(sorted(TAXONOMIES))}")
    taxonomy = Taxonomy.model_validate_json((TAXONOMY_DIR / name).read_text())
    if taxonomy.ref != ref:
        raise ValueError(f"{name} declares itself {taxonomy.ref}, not {ref}")
    return taxonomy


@cache
def load_mapping(source: str, target: str) -> TaxonomyMapping:
    name = _MAPPINGS.get((source, target))
    if name is None:
        raise ValueError(f"no mapping from {source} to {target}")
    mapping = TaxonomyMapping.model_validate_json((TAXONOMY_DIR / name).read_text())
    if (mapping.source, mapping.target) != (source, target):
        raise ValueError(
            f"{name} maps {mapping.source} to {mapping.target}, not {source} to {target}"
        )
    return mapping


def map_dimension(dimension: str, source: str, target: str) -> str:
    return load_mapping(source, target).dimensions[dimension]


def map_check(check: str, source: str, target: str) -> str:
    return load_mapping(source, target).checks[check]


def validate_against(taxonomy_ref: str, dimension: str, check: str | None) -> None:
    """Raise ValueError unless `dimension` and `check` belong to the named taxonomy."""
    taxonomy = load_taxonomy(taxonomy_ref)
    if dimension not in taxonomy.dimension_ids():
        raise ValueError(f"{dimension!r} is not a dimension of {taxonomy_ref}")
    if check is not None and check not in taxonomy.check_ids():
        raise ValueError(f"{check!r} is not a check of {taxonomy_ref}")


def as_json(path: Path) -> dict[str, object]:
    """Raw file contents, for tests and tooling that compare against the LaTeX source."""
    loaded: dict[str, object] = json.loads(path.read_text())
    return loaded
