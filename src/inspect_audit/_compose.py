"""The audit sandbox: the benchmark's own compose, untouched, plus an `auditor` service.

Their services keep their names, so their `default` stays the default and their tools
and scorers address the box they always did. Ours is added beside it on its own
network, with internet. Docker merges the two (`include:`) and `docker compose
config` flattens the result into one plain file, which the Docker provider runs as
is and the k8s converter (which rejects `include:`) also accepts.
"""

import subprocess
from pathlib import Path
from typing import Any

import yaml
from inspect_ai.dataset import Sample
from inspect_ai.util import ComposeConfig, SandboxEnvironmentSpec

from .containers import AUDITOR_DOCKERFILE

AUDITOR = "auditor"
AUDITOR_NETWORK = "auditor_net"


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
    benchmark gives samples different environments, which v1 does not support.
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
    """Their compose (if they have one) plus the `auditor` service, flattened into `stage/compose.yaml`."""
    stage.mkdir(parents=True, exist_ok=True)
    services = (
        yaml.safe_load(benchmark_compose.read_text()).get("services") or {}
        if benchmark_compose is not None
        else {}
    )
    if AUDITOR in services:
        raise UnsupportedBenchmarkError(f"the benchmark already has a service named {AUDITOR!r}")

    auditor: dict[str, Any] = {
        "command": "tail -f /dev/null",
        "init": True,
        "networks": [AUDITOR_NETWORK],
    }
    if auditor_image is not None:
        auditor["image"] = auditor_image
    else:
        (stage / "auditor.Dockerfile").write_text(AUDITOR_DOCKERFILE)
        auditor["build"] = {"context": str(stage), "dockerfile": "auditor.Dockerfile"}
        auditor["x-local"] = True
    wrapper: dict[str, Any] = {
        "services": {AUDITOR: auditor},
        "networks": {AUDITOR_NETWORK: {}},
    }
    if benchmark_compose is not None:
        wrapper = {"include": [str(benchmark_compose)], **wrapper}
    else:
        # a benchmark with no box of its own: Inspect needs a `default`, and marking
        # ours default would have the k8s converter rename it. An idle, offline
        # placeholder keeps every service name the same on Docker and k8s; no tool
        # is given access to it.
        wrapper["services"]["default"] = {
            "image": "python:3.12-slim",
            "command": "tail -f /dev/null",
            "init": True,
            "network_mode": "none",
        }
    wrapper_path = stage / "wrapper.compose.yaml"
    wrapper_path.write_text(yaml.safe_dump(wrapper, sort_keys=False))

    flattened = subprocess.run(
        ["docker", "compose", "-f", str(wrapper_path), "config"],
        check=False,
        capture_output=True,
        text=True,
    )
    if flattened.returncode != 0:
        raise UnsupportedBenchmarkError(
            f"docker compose could not merge the benchmark's compose: {flattened.stderr.strip()}"
        )
    merged = yaml.safe_load(flattened.stdout)
    # names Docker derives from the wrapper's project: the project is Inspect's to
    # name, and the k8s converter rejects a network that carries one
    merged.pop("name", None)
    for network in (merged.get("networks") or {}).values():
        if isinstance(network, dict):
            network.pop("name", None)
    path = stage / "compose.yaml"
    path.write_text(yaml.safe_dump(merged, sort_keys=False))
    return SandboxEnvironmentSpec("docker", str(path))
