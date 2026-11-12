"""The package's import graph: lower layers never import higher ones, and no new cycles."""

import ast
from pathlib import Path

ROOT = Path(__file__).parents[1] / "src" / "inspect_audit"

# Running jobs, policy, keys and prices know nothing about the agents that use them.
LOWER = ("dispatch", "_runs", "_policy", "_prices", "_log_sources")
HIGHER = ("_audit", "_registry", "_investigator", "_task", "_publishing", "_findings", "_report")

# Cycles that exist today. Each consolidation step removes one; this list only shrinks.
KNOWN_CYCLES: list[set[str]] = [
    # findings, register and publishing all reach back into the top-level agent
    {"_audit", "_publishing", "_findings", "_register", "publishers.latex", "publishers.gl"},
]


def _modules() -> dict[str, Path]:
    found: dict[str, Path] = {}
    for path in ROOT.rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        parts = path.relative_to(ROOT).with_suffix("").parts
        found[".".join(p for p in parts if p != "__init__") or "__init__"] = path
    return found


def _graph() -> dict[str, set[str]]:
    modules = _modules()
    graph: dict[str, set[str]] = {name: set() for name in modules}
    for name, path in modules.items():
        package = name.split(".")[:-1] if path.name != "__init__.py" else name.split(".")
        # every import counts, including ones inside functions: a lazy import is still a dependency
        for node in ast.walk(ast.parse(path.read_text())):
            targets: list[str] = []
            if isinstance(node, ast.ImportFrom) and node.level:
                base = package[: len(package) - node.level + 1]
                stem = ".".join([*base, *([node.module] if node.module else [])])
                targets = [f"{stem}.{a.name}".strip(".") for a in node.names] + [stem]
            elif isinstance(node, ast.ImportFrom) and (node.module or "").startswith(
                "inspect_audit"
            ):
                stem = node.module.removeprefix("inspect_audit").strip(".")  # type: ignore[union-attr]
                targets = [f"{stem}.{a.name}".strip(".") for a in node.names] + [stem]
            for target in targets:
                if target in modules and target != name:
                    graph[name].add(target)
                    break
    return graph


def _cycles(graph: dict[str, set[str]]) -> list[set[str]]:
    index: dict[str, int] = {}
    low: dict[str, int] = {}
    stack: list[str] = []
    found: list[set[str]] = []

    def visit(node: str) -> None:
        index[node] = low[node] = len(index)
        stack.append(node)
        for nxt in graph[node]:
            if nxt not in index:
                visit(nxt)
                low[node] = min(low[node], low[nxt])
            elif nxt in stack:
                low[node] = min(low[node], index[nxt])
        if low[node] == index[node]:
            component = set()
            while True:
                member = stack.pop()
                component.add(member)
                if member == node:
                    break
            if len(component) > 1:
                found.append(component)

    for node in graph:
        if node not in index:
            visit(node)
    return found


def _top(name: str) -> str:
    return name.split(".")[0]


def test_lower_layers_never_import_higher_ones() -> None:
    graph = _graph()
    upward = sorted(
        f"{source} imports {target}"
        for source, targets in graph.items()
        for target in targets
        if _top(source) in LOWER and _top(target) in HIGHER
    )
    assert upward == []


def test_no_import_cycles_beyond_the_known_ones() -> None:
    cycles = _cycles(_graph())
    new = [c for c in cycles if c not in KNOWN_CYCLES]
    gone = [c for c in KNOWN_CYCLES if c not in cycles]
    assert new == [], f"new import cycles: {new}"
    assert gone == [], f"these cycles are gone; remove them from KNOWN_CYCLES: {gone}"
