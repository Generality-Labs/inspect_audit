"""The benchmark's code for the investigator to read: the task's own package, followed by import.

Starting from the task's file, every import that resolves inside the same top-level
package is followed, file by file -- including imports inside functions, which a
scorer often makes lazily. Modules Python actually loaded from the package are added
as a backstop for imports by computed name. Each copied module's folder brings its
non-Python files (templates, data) and any data-only subfolders, under a size cap.
Other packages are not copied: their exact install specs are recorded so the investigator,
which has internet, can install and read precisely the versions that ran.
"""

import ast
import io
import json
import sys
import tarfile
from dataclasses import dataclass, field
from importlib.metadata import PackageNotFoundError, distribution, packages_distributions, version
from pathlib import Path

MAX_FILE_BYTES = 20 * 1024 * 1024
MAX_TOTAL_BYTES = 100 * 1024 * 1024


@dataclass
class SourceCopy:
    """What the investigator gets of the benchmark's code, and what it does not."""

    root: Path
    """Directory the copied paths are relative to (the package's parent)."""
    files: list[Path] = field(default_factory=list)
    skipped: dict[str, str] = field(default_factory=dict)
    """Relative path -> why it was left out."""
    loaded_only: list[str] = field(default_factory=list)
    """Modules Python loaded that the import graph did not reach."""
    install: dict[str, str] = field(default_factory=dict)
    """Distribution -> exact install spec, for the benchmark and what its code imports."""

    def manifest(self) -> dict[str, object]:
        return {
            "copied": sorted(str(f.relative_to(self.root)) for f in self.files),
            "skipped": self.skipped,
            "loaded_but_not_imported_statically": self.loaded_only,
            "install": self.install,
        }

    def archive(self) -> bytes:
        buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
            for file in self.files:
                tar.add(file, arcname=f"benchmark/{file.relative_to(self.root)}")
        return buffer.getvalue()


def package_root(task_file: Path) -> Path | None:
    """The top-level package directory holding the task file, if it is in one."""
    directory = task_file.parent
    if not (directory / "__init__.py").is_file():
        return None
    while (directory.parent / "__init__.py").is_file():
        directory = directory.parent
    return directory


def _module_file(base: Path, dotted: str) -> Path | None:
    path = base.joinpath(*dotted.split("."))
    if path.with_suffix(".py").is_file():
        return path.with_suffix(".py")
    if (path / "__init__.py").is_file():
        return path / "__init__.py"
    return None


def _module_name(base: Path, file: Path) -> str:
    parts = file.relative_to(base).with_suffix("").parts
    return ".".join(parts[:-1] if parts[-1] == "__init__" else parts)


def _imports(file: Path, base: Path) -> tuple[set[str], set[str]]:
    """Dotted names a file imports, absolute: (all of them, top-level names)."""
    tree = ast.parse(file.read_text(encoding="utf-8"))
    current = _module_name(base, file).split(".")
    package = current if file.name == "__init__.py" else current[:-1]
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                anchor = package[: len(package) - (node.level - 1)] if node.level > 1 else package
                module = ".".join([*anchor, *([node.module] if node.module else [])])
            else:
                module = node.module or ""
            if module:
                names.add(module)
                # `from pkg.x import y` may name a submodule y
                names.update(f"{module}.{alias.name}" for alias in node.names)
    return names, {n.split(".")[0] for n in names}


def _install_spec(dist: str) -> str:
    """An exact pip requirement for an installed distribution (PEP 610 for non-index installs)."""
    text = distribution(dist).read_text("direct_url.json")
    if text:
        direct = json.loads(text)
        url = str(direct.get("url", ""))
        if "vcs_info" in direct:
            vcs = direct["vcs_info"]
            ref = vcs.get("commit_id") or vcs.get("requested_revision")
            return f"{dist} @ {vcs['vcs']}+{url}" + (f"@{ref}" if ref else "")
        if url.startswith("file:"):
            return f"{dist}=={version(dist)} (installed from a local path: {url})"
        return f"{dist} @ {url}"
    return f"{dist}=={version(dist)}"


def _data_files(directory: Path, copied_dirs: set[Path]) -> list[Path]:
    """Non-Python files in a code folder, and everything in its data-only subfolders."""
    found: list[Path] = []
    for path in sorted(directory.iterdir()):
        if path.name == "__pycache__":
            continue
        if path.is_file() and path.suffix != ".py":
            found.append(path)
        elif path.is_dir() and path not in copied_dirs:
            inside = [
                p for p in sorted(path.rglob("*")) if p.is_file() and "__pycache__" not in p.parts
            ]
            # a data folder holds no code; one with Python under it is someone else's module
            if not any(p.suffix == ".py" for p in inside):
                found.extend(inside)
    return found


def collect_source(task_file: Path) -> SourceCopy:
    """The benchmark's code reachable from its task file, within its own package."""
    package = package_root(task_file)
    if package is None:
        # a lone task file: its folder is the benchmark
        return collect_directory(task_file.parent)

    base, name = package.parent, package.name
    copy = SourceCopy(root=base)
    modules: set[Path] = set()
    external: set[str] = set()
    queue = [task_file.resolve()]
    while queue:
        file = queue.pop()
        if file in modules:
            continue
        modules.add(file)
        # importing pkg.a.b runs pkg/__init__.py and pkg/a/__init__.py too
        parent = file.parent
        while parent != base:
            init = parent / "__init__.py"
            if init.is_file() and init not in modules:
                queue.append(init)
            parent = parent.parent
        dotted, tops = _imports(file, base)
        external |= tops - {name}
        for module in dotted:
            if module.split(".")[0] == name and (found := _module_file(base, module)):
                queue.append(found.resolve())

    # backstop: anything Python loaded from the package that the graph missed
    for module_name, module in list(sys.modules.items()):
        location = getattr(module, "__file__", None)
        if module_name.split(".")[0] == name and location:
            path = Path(location).resolve()
            if path.suffix == ".py" and path not in modules and package in path.parents:
                modules.add(path)
                copy.loaded_only.append(module_name)

    task_dir = task_file.resolve().parent
    copied_dirs = {m.parent for m in modules}
    files = set(modules)
    files.update(p for p in task_dir.rglob("*") if p.is_file() and "__pycache__" not in p.parts)
    for directory in copied_dirs:
        files.update(_data_files(directory, copied_dirs))
    copy.files = sorted(files)

    distributions = packages_distributions()
    for top in sorted({name, "inspect_ai", *external}):
        for dist in distributions.get(top, []):
            try:
                copy.install[dist] = _install_spec(dist)
            except PackageNotFoundError:
                continue
    return _capped(copy)


def collect_directory(directory: Path) -> SourceCopy:
    """A declared source directory, whole."""
    copy = SourceCopy(root=directory)
    copy.files = [
        p for p in sorted(directory.rglob("*")) if p.is_file() and "__pycache__" not in p.parts
    ]
    return _capped(copy)


def _capped(copy: SourceCopy) -> SourceCopy:
    kept: list[Path] = []
    total = 0
    for file in copy.files:
        size = file.stat().st_size
        relative = str(file.relative_to(copy.root))
        if size > MAX_FILE_BYTES:
            copy.skipped[relative] = f"{size // 2**20} MiB, over the per-file cap"
        elif total + size > MAX_TOTAL_BYTES:
            copy.skipped[relative] = "over the total size cap"
        else:
            kept.append(file)
            total += size
    copy.files = kept
    return copy
