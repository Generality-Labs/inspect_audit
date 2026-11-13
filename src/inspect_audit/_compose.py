"""The investigation sandbox: the benchmark's own compose, untouched, plus an `investigator` service.

Their services keep their names, so their `default` stays the default and their tools
and scorers address the box they always did. Ours is added beside it with internet.
The two are merged here into one plain file, their relative paths made absolute so
the file can live elsewhere: the Docker provider runs it as is, and the k8s
converter (which refuses `include:`) accepts it, with no docker needed to merge.

On Docker only the investigator reaches the internet. On Kubernetes internet is granted
per sandbox, so their box has it during an investigation too; the investigator is told so.
"""

import os
from pathlib import Path
from typing import Any

import yaml
from inspect_ai.dataset import Sample
from inspect_ai.util import ComposeConfig, SandboxEnvironmentSpec

from .containers import INVESTIGATOR_DOCKERFILE, INVESTIGATOR_REQUIREMENTS

INVESTIGATOR = "investigator"


class UnsupportedBenchmarkError(ValueError):
    """The benchmark is outside what the investigator supports; the message says why."""


def sandbox_spec(value: object) -> SandboxEnvironmentSpec | None:
    """A sandbox declaration (`"docker"`, `(type, config)` or a spec) as a spec."""
    if value is None or isinstance(value, SandboxEnvironmentSpec):
        return value
    if isinstance(value, str):
        return SandboxEnvironmentSpec(value)
    if isinstance(value, tuple) and len(value) == 2:
        return SandboxEnvironmentSpec(str(value[0]), value[1])
    raise UnsupportedBenchmarkError(f"unrecognised sandbox declaration: {value!r}")


def their_sandbox(task_sandbox: object, samples: list[Sample]) -> SandboxEnvironmentSpec | None:
    """The one sandbox every investigated sample runs in, None if it has none, or a loud refusal.

    A sample-level sandbox equal to the task's is fine; any other means the
    benchmark gives samples different environments, which the investigator does not support.
    """
    task_spec = sandbox_spec(task_sandbox)
    distinct: dict[str, SandboxEnvironmentSpec] = {}
    for sample in samples:
        spec = sandbox_spec(sample.sandbox)
        if spec is not None:
            distinct[repr(spec)] = spec
    if task_spec is not None:
        distinct.pop(repr(task_spec), None)
    elif len(distinct) == 1:
        # no task-level sandbox: every sample declaring the same one is the same thing
        [(_, task_spec)] = distinct.items()
        distinct = {}
    if distinct:
        raise UnsupportedBenchmarkError(
            "the benchmark gives its samples their own sandboxes "
            f"({len(distinct)} distinct); per-sample environments are not supported"
        )
    if task_spec is None:
        return None  # no box of theirs: the investigation runs with the investigator's alone
    if task_spec.type not in {"docker", "k8s"}:
        raise UnsupportedBenchmarkError(
            f"the benchmark's sandbox is {task_spec.type!r}; supported providers are docker and k8s"
        )
    return task_spec


def their_compose(spec: SandboxEnvironmentSpec, compose: str | None, stage: Path) -> Path:
    """The benchmark's compose file: declared by the task, or by the operator.

    Inspect looks for an implicit compose file or Dockerfile beside a task that
    names only `"docker"`; the investigator does not guess, so such a task needs `compose=`.
    """
    config = spec.config
    if compose is not None:
        if config is not None and not (
            isinstance(config, str) and Path(config).resolve() == Path(compose).resolve()
        ):
            raise UnsupportedBenchmarkError(
                f"compose={compose!r} differs from the benchmark's own sandbox config {config!r}"
            )
        path = Path(compose)
    elif isinstance(config, ComposeConfig):
        path = stage / "benchmark.compose.yaml"
        path.write_text(
            yaml.safe_dump(
                config.model_dump(mode="json", by_alias=True, exclude_none=True), sort_keys=False
            )
        )
    elif isinstance(config, str):
        path = Path(config)
    else:
        raise UnsupportedBenchmarkError(
            "the benchmark's sandbox is a bare 'docker' with no compose file; pass compose= "
            "with the compose file it runs (Inspect would otherwise pick one implicitly)"
        )
    if not path.is_file():
        raise UnsupportedBenchmarkError(f"compose file not found: {path}")
    if path.name.lower().endswith("dockerfile") or path.name == "Dockerfile":
        raise UnsupportedBenchmarkError(
            f"the benchmark's sandbox is a Dockerfile ({path}); pass compose= with a compose file"
        )
    return path.resolve()


def investigation_compose(
    benchmark_compose: Path | None, *, stage: Path, investigator_image: str | None = None
) -> SandboxEnvironmentSpec:
    """Their compose (if they have one) plus the `investigator` service, merged into `stage/compose.yaml`."""
    stage.mkdir(parents=True, exist_ok=True)
    merged: dict[str, Any] = (
        _resolved(benchmark_compose) if benchmark_compose is not None else {"services": {}}
    )
    services = merged["services"]
    if INVESTIGATOR in services:
        raise UnsupportedBenchmarkError(
            f"the benchmark already has a service named {INVESTIGATOR!r}"
        )

    # Use the shared bridge to avoid allocating a subnet per sample.
    # Benchmark access uses exec, independent of the investigator's network.
    investigator: dict[str, Any] = {
        "command": "tail -f /dev/null",
        "init": True,
        "network_mode": "bridge",
    }
    if investigator_image is not None:
        investigator["image"] = investigator_image
    else:
        (stage / "investigator.Dockerfile").write_text(INVESTIGATOR_DOCKERFILE)
        (stage / "investigator.requirements.txt").write_text(INVESTIGATOR_REQUIREMENTS)
        investigator["build"] = {"context": str(stage), "dockerfile": "investigator.Dockerfile"}
        investigator["x-local"] = True
    if benchmark_compose is None:
        # A separate default service prevents the k8s converter from renaming ours.
        merged["services"]["default"] = {
            "image": "python:3.12-slim",
            "command": "tail -f /dev/null",
            "init": True,
            "network_mode": "none",
        }
    # Name the implicit default network explicitly: Hawk rejects unnamed networks
    # when another service sets network_mode.
    unnamed = [
        service
        for service in merged["services"].values()
        if isinstance(service, dict)
        and service.get("network_mode") is None
        and not service.get("networks")
    ]
    if unnamed:
        merged.setdefault("networks", {}).setdefault("default", {})
        for service in unnamed:
            service["networks"] = ["default"]
    if benchmark_compose is not None:
        default = their_default(
            merged["services"],
            included="include" in (yaml.safe_load(benchmark_compose.read_text()) or {}),
        )
        if default != "default":
            # said outright, so adding the investigator cannot move it under either provider
            merged["services"][default]["x-default"] = True
    merged["services"][INVESTIGATOR] = investigator
    # the project is Inspect's to name
    merged.pop("name", None)
    path = stage / "compose.yaml"
    path.write_text(yaml.safe_dump(merged, sort_keys=False))
    return SandboxEnvironmentSpec("docker", str(path))


def their_default(services: dict[str, Any], *, included: bool) -> str:
    """The service the benchmark's own runs used as `default`, by the providers' own rules.

    Docker: a service named `default`, else the one marked `x-default`, else Inspect
    refuses to run (`inspect_ai/util/_sandbox/docker/docker.py`). Kubernetes, which
    converts the compose: the same two, else the first service in the file
    (`k8s_sandbox/compose/_converter.py`). So with neither, the benchmark can only have
    run converted to Kubernetes, with its first service as default. A compose that
    includes others cannot be converted, so with no default it could not have run at all.
    """
    if "default" in services:
        return "default"
    marked = [n for n, s in services.items() if isinstance(s, dict) and s.get("x-default")]
    if len(marked) > 1:
        raise UnsupportedBenchmarkError(f"the benchmark marks several services x-default: {marked}")
    if marked:
        return marked[0]
    if not services:
        raise UnsupportedBenchmarkError("the benchmark's compose defines no services")
    if included:
        raise UnsupportedBenchmarkError(
            "the benchmark's compose names no default service (none called 'default', none "
            "marked x-default) and uses include:, so neither Docker nor the Kubernetes converter "
            "could have run it: the default its logs used cannot be known"
        )
    return next(iter(services))


def _resolved(path: Path, seen: tuple[Path, ...] = ()) -> dict[str, Any]:
    """Their compose as one plain file: `include:` and `extends:` resolved, host paths absolute.

    Done here rather than by `docker compose config`, which needs Docker (the Hawk runner
    has none) and substitutes `${VAR}` with whatever the shell has, baking in defaults the
    benchmark meant to set per sample (CVE-bench's Kali image, FRICTION 58). Each file's
    relative paths resolve against its own folder, as in compose.
    """
    path = path.resolve()
    if path in seen:
        raise UnsupportedBenchmarkError(f"compose files include each other in a loop: {path}")
    if not path.is_file():
        raise UnsupportedBenchmarkError(f"compose file not found: {path}")
    compose = yaml.safe_load(path.read_text()) or {}
    if not isinstance(compose, dict):
        raise UnsupportedBenchmarkError(f"{path} is not a compose file")
    includes = compose.pop("include", None) or []
    services: dict[str, Any] = compose.get("services") or {}
    compose["services"] = services
    for name, service in list(services.items()):
        if isinstance(service, dict) and "extends" in service:
            services[name] = _extended(service, path, (*seen, path))
    compose = _absolute(compose, path.parent)
    for entry in includes:
        for included in _include_paths(entry, path):
            other = _resolved(included, (*seen, path))
            for section, items in other.items():
                if not isinstance(items, dict):
                    continue
                mine = compose.setdefault(section, {})
                clash = sorted(set(mine) & set(items))
                if clash and section == "services":
                    raise UnsupportedBenchmarkError(
                        f"{included} defines service(s) {clash} that {path} already has"
                    )
                for key, value in items.items():
                    mine.setdefault(key, value)
    return compose


def _include_paths(entry: object, owner: Path) -> list[Path]:
    """The files one `include:` entry names, against the including file's folder."""
    if isinstance(entry, dict) and (entry.get("project_directory") or entry.get("env_file")):
        raise UnsupportedBenchmarkError(
            f"{owner}: include with project_directory or env_file is not supported"
        )
    raw = entry.get("path") if isinstance(entry, dict) else entry
    paths = raw if isinstance(raw, list) else [raw]
    if not paths or not all(isinstance(p, str) and "${" not in p for p in paths):
        raise UnsupportedBenchmarkError(f"{owner}: unsupported include entry {entry!r}")
    return [(owner.parent / os.path.expanduser(str(p))).resolve() for p in paths]


# keys compose merges by key when a service extends another; the rest are replaced
_MAPPINGS = {
    "annotations",
    "build",
    "deploy",
    "environment",
    "extra_hosts",
    "healthcheck",
    "labels",
    "logging",
    "sysctls",
    "ulimits",
}
# sequences merged by mount target; other sequences are appended without repeats
_BY_TARGET = {"devices", "tmpfs", "volumes"}
_REPLACED = {"command", "entrypoint"}


def _extended(service: dict[str, Any], owner: Path, seen: tuple[Path, ...]) -> dict[str, Any]:
    """A service with its `extends:` base merged beneath it, as compose does."""
    extends = service.pop("extends")
    if isinstance(extends, str):
        base_file, base_name = owner, extends
    elif isinstance(extends, dict) and isinstance(extends.get("service"), str):
        file = extends.get("file")
        base_file = (owner.parent / file).resolve() if isinstance(file, str) else owner
        base_name = extends["service"]
    else:
        raise UnsupportedBenchmarkError(f"{owner}: unsupported extends {extends!r}")
    if base_file == owner:
        if len(seen) > 32:
            raise UnsupportedBenchmarkError(f"{owner}: extends nests too deeply")
        siblings = (yaml.safe_load(owner.read_text()) or {}).get("services") or {}
        if not isinstance(siblings.get(base_name), dict):
            raise UnsupportedBenchmarkError(f"{owner}: extends unknown service {base_name!r}")
        base = dict(siblings[base_name])
        if "extends" in base:
            base = _extended(base, owner, (*seen, owner))
    else:
        other = _resolved(base_file, seen)
        if not isinstance(other["services"].get(base_name), dict):
            raise UnsupportedBenchmarkError(f"{base_file}: no service {base_name!r} to extend")
        base = other["services"][base_name]  # already absolute against its own folder
    # a base's links to other services stay in its own file, as in compose
    base = {k: v for k, v in base.items() if k not in {"depends_on", "links", "volumes_from"}}
    return _merge_service(base, service)


def _as_mapping(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    out: dict[str, Any] = {}
    for item in value or []:
        key, sep, rest = str(item).partition("=")
        out[key] = rest if sep else None
    return out


def _target(mount: Any) -> str:
    if isinstance(mount, dict):
        return str(mount.get("target"))
    parts = str(mount).split(":")
    return parts[1] if len(parts) > 1 else parts[0]


def _merge_service(base: dict[str, Any], local: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in local.items():
        old = merged.get(key)
        if key not in merged:
            merged[key] = value
        elif key in _MAPPINGS and not isinstance(value, str) and not isinstance(old, str):
            merged[key] = {**_as_mapping(old), **_as_mapping(value)}
        elif key in _BY_TARGET and isinstance(value, list) and isinstance(old, list):
            mine = {_target(v) for v in value}
            merged[key] = [v for v in old if _target(v) not in mine] + value
        elif key not in _REPLACED and isinstance(value, list) and isinstance(old, list):
            merged[key] = old + [v for v in value if v not in old]
        else:
            merged[key] = value
    return merged


def _absolute(compose: dict[str, Any], base: Path) -> dict[str, Any]:
    """Their compose with every relative host path made absolute against its folder.

    The paths compose resolves against the file's own folder: build contexts, bind
    mount sources, env files, and top-level configs and secrets read from files.
    Anything else is copied as written. `include:` and `extends:` are resolved
    before this, by `_resolved`.
    """

    def where(path: str) -> str:
        expanded = os.path.expanduser(path)
        if os.path.isabs(expanded) or "${" in path or "://" in path:
            return path
        return str((base / expanded).resolve())

    services = compose.get("services") or {}
    for service in services.values():
        if not isinstance(service, dict):
            continue
        build = service.get("build")
        if isinstance(build, str):
            service["build"] = where(build)
        elif isinstance(build, dict) and isinstance(build.get("context"), str):
            build["context"] = where(build["context"])
        elif isinstance(build, dict):
            build["context"] = str(base.resolve())
        env_file = service.get("env_file")
        if isinstance(env_file, str):
            service["env_file"] = where(env_file)
        elif isinstance(env_file, list):
            service["env_file"] = [
                where(e)
                if isinstance(e, str)
                else {**e, "path": where(e["path"])}
                if isinstance(e, dict) and isinstance(e.get("path"), str)
                else e
                for e in env_file
            ]
        if "volumes" in service:
            service["volumes"] = [_mount(v, where) for v in service["volumes"] or []]
    for section in ("configs", "secrets"):
        for entry in (compose.get(section) or {}).values():
            if isinstance(entry, dict) and isinstance(entry.get("file"), str):
                entry["file"] = where(entry["file"])
    compose["services"] = services
    return compose


def _mount(volume: Any, where: Any) -> Any:
    """A mount with a host-path source made absolute; a named volume as written."""
    if isinstance(volume, str):
        source, sep, rest = volume.partition(":")
        # a host path starts with . / or ~; anything else names a volume
        if sep and source.startswith((".", "/", "~")):
            return f"{where(source)}:{rest}"
    elif isinstance(volume, dict) and volume.get("type") == "bind":
        return {**volume, "source": where(str(volume.get("source", "")))}
    return volume
