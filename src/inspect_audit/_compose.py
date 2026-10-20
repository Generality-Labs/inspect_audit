"""The audit sandbox: the benchmark's own compose, untouched, plus an `auditor` service.

Their services keep their names, so their `default` stays the default and their tools
and scorers address the box they always did. Ours is added beside it with internet.
The two are merged here into one plain file, their relative paths made absolute so
the file can live elsewhere: the Docker provider runs it as is, and the k8s
converter (which refuses `include:`) accepts it, with no docker needed to merge.

On Docker only the auditor reaches the internet. On Kubernetes internet is granted
per sandbox, so their box has it during an audit too; the auditor is told so.
"""

import os
from pathlib import Path
from typing import Any

import yaml
from inspect_ai.dataset import Sample
from inspect_ai.util import ComposeConfig, SandboxEnvironmentSpec

from .containers import AUDITOR_DOCKERFILE

AUDITOR = "auditor"


class UnsupportedBenchmarkError(ValueError):
    """The benchmark is outside what the auditor supports; the message says why."""


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
    """The one sandbox every audited sample runs in, None if it has none, or a loud refusal.

    A sample-level sandbox equal to the task's is fine; any other means the
    benchmark gives samples different environments, which the auditor does not support.
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
        return None  # no box of theirs: the audit runs with the auditor's alone
    if task_spec.type != "docker":
        raise UnsupportedBenchmarkError(
            f"the benchmark's sandbox is {task_spec.type!r}; the auditor needs its "
            "Docker compose declaration (k8s runs convert that same compose)"
        )
    return task_spec


def their_compose(spec: SandboxEnvironmentSpec, compose: str | None, stage: Path) -> Path:
    """The benchmark's compose file: declared by the task, or by the operator.

    Inspect looks for an implicit compose file or Dockerfile beside a task that
    names only `"docker"`; the auditor does not guess, so such a task needs `compose=`.
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


def audit_compose(
    benchmark_compose: Path | None, *, stage: Path, auditor_image: str | None = None
) -> SandboxEnvironmentSpec:
    """Their compose (if they have one) plus the `auditor` service, merged into `stage/compose.yaml`."""
    stage.mkdir(parents=True, exist_ok=True)
    services = (
        yaml.safe_load(benchmark_compose.read_text()).get("services") or {}
        if benchmark_compose is not None
        else {}
    )
    if AUDITOR in services:
        raise UnsupportedBenchmarkError(f"the benchmark already has a service named {AUDITOR!r}")

    # egress through Docker's shared default bridge, not a named network: a named
    # network takes a /24 per sample from Docker's ~31-subnet pool, exhausts it at
    # concurrency and leaks on a hard kill (an earlier auditor hit this). Their box stays on its own
    # network or none; the auditor reaches it through exec, never the network.
    auditor: dict[str, Any] = {
        "command": "tail -f /dev/null",
        "init": True,
        "network_mode": "bridge",
    }
    if auditor_image is not None:
        auditor["image"] = auditor_image
    else:
        (stage / "auditor.Dockerfile").write_text(AUDITOR_DOCKERFILE)
        auditor["build"] = {"context": str(stage), "dockerfile": "auditor.Dockerfile"}
        auditor["x-local"] = True
    merged: dict[str, Any] = (
        _absolute(yaml.safe_load(benchmark_compose.read_text()) or {}, benchmark_compose.parent)
        if benchmark_compose is not None
        else {"services": {}}
    )
    if benchmark_compose is None:
        # a benchmark with no box of its own: Inspect needs a `default`, and marking
        # ours default would have the k8s converter rename it. An idle, offline
        # placeholder keeps every service name the same on Docker and k8s; no tool
        # is given access to it.
        merged["services"]["default"] = {
            "image": "python:3.12-slim",
            "command": "tail -f /dev/null",
            "init": True,
            "network_mode": "none",
        }
    merged["services"][AUDITOR] = auditor
    # the project is Inspect's to name
    merged.pop("name", None)
    path = stage / "compose.yaml"
    path.write_text(yaml.safe_dump(merged, sort_keys=False))
    return SandboxEnvironmentSpec("docker", str(path))


def _absolute(compose: dict[str, Any], base: Path) -> dict[str, Any]:
    """Their compose with every relative host path made absolute against its folder.

    The paths compose resolves against the file's own folder: build contexts, bind
    mount sources, env files, and top-level configs and secrets read from files.
    Anything else is copied as written. A compose that includes or extends another
    file is refused rather than half-merged.
    """
    if "include" in compose:
        raise UnsupportedBenchmarkError(
            "the benchmark's compose uses include:, which is not supported"
        )

    def where(path: str) -> str:
        expanded = os.path.expanduser(path)
        if os.path.isabs(expanded) or "${" in path or "://" in path:
            return path
        return str((base / expanded).resolve())

    services = compose.get("services") or {}
    for name, service in services.items():
        if not isinstance(service, dict):
            continue
        if "extends" in service:
            raise UnsupportedBenchmarkError(
                f"service {name!r} uses extends:, which is not supported"
            )
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
