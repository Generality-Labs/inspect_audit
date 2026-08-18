"""Running an audit alongside the environment it is auditing.

An auditor needs tools the benchmark's image does not have — `inspect_ai` to read logs,
curl to read sources, the skills — while the benchmark's environment must stay as it
was, because "what could the evaluated agent reach from here?" is only answerable
against the real thing.

So both run as services of one Docker project: the audited task's services with their
definitions preserved, plus one for the auditor. The agent gets a shell in each and the
harness is the only bridge between them.

The auditor's service must be the one named `default`, because Inspect treats the name
`default` as an alias for "the default environment" rather than as a service lookup
(`util/_sandbox/context.py`). A benchmark service called `default` would therefore be
unaddressable, so it is renamed — the one edit made to their configuration, with
`depends_on` references updated to match.

Nothing of the audit crosses into the benchmark's services: `Sample.files` keys without
an `envname:` prefix are written to the default environment, which is the auditor's.
"""

from pathlib import Path
from typing import Any

import yaml
from inspect_ai import Task
from inspect_ai.util import SandboxEnvironmentSpec, SandboxEnvironmentType
from inspect_ai.util._sandbox.compose import is_dockerfile

from ._sandbox import DOCKERFILE, audit_sandbox, task_requirements

__all__ = ["BENCHMARK_SERVICE", "audit_compose"]

BENCHMARK_SERVICE = "benchmark"
"""The name the audited task's `default` service is given, so it stays addressable."""

AUDITOR_NETWORK = "inspect_audit"

AUDITOR_SERVICE: dict[str, Any] = {
    "build": {"context": None, "dockerfile": "Dockerfile"},
    "command": "sleep infinity",
    "init": True,
    # Explicit, and stripped from the benchmark's services below: an `x-default` on
    # any service beats one merely *named* `default`, and a benchmark that declares
    # its own would otherwise receive the audit's files -- including the sliced logs,
    # which carry the answer whose reachability is the question.
    "x-default": True,
    # Our own network. Sharing the project default would put a live host running curl,
    # git and a full Python next to the benchmark container, which was not there during
    # the eval and which a reachability audit would find.
    "networks": [AUDITOR_NETWORK],
    "stop_grace_period": "1s",
}

# Paths inside a compose file are relative to that file's directory. The merged file
# lives somewhere else, so they are re-anchored; otherwise the build either fails or
# silently uses the wrong directory.
_RELATIVE_KEYS = ("env_file", "dockerfile")


def _anchor(value: Any, base: Path) -> Any:
    if isinstance(value, str) and not value.startswith(("/", "$")) and ":" not in value:
        return str((base / value).resolve())
    return value


def _anchor_service(service: dict[str, Any], base: Path) -> dict[str, Any]:
    """Re-anchor a service's relative paths against the original compose's directory."""
    out = dict(service)
    build = out.get("build")
    if isinstance(build, str):
        out["build"] = _anchor(build, base)
    elif isinstance(build, dict):
        build = dict(build)
        if "context" in build:
            build["context"] = _anchor(build["context"], base)
        out["build"] = build
    for key in _RELATIVE_KEYS:
        if isinstance(out.get(key), str):
            out[key] = _anchor(out[key], base)
        elif isinstance(out.get(key), list):
            out[key] = [_anchor(v, base) for v in out[key]]
    if isinstance(out.get("volumes"), list):
        out["volumes"] = [_anchor_volume(v, base) for v in out["volumes"]]
    return out


def _anchor_volume(volume: Any, base: Path) -> Any:
    """Re-anchor a bind mount's host path, in either compose syntax.

    Missing the long form is not a cosmetic bug: compose resolves the relative source
    against the merged file's directory, docker then creates it empty, and the
    benchmark container silently loses the data that was mounted there.
    """
    if isinstance(volume, str) and volume.startswith("."):
        host, _, rest = volume.partition(":")
        return f"{_anchor(host, base)}:{rest}"
    if isinstance(volume, dict) and volume.get("type") == "bind":
        bound = dict(volume)
        bound["source"] = _anchor(bound.get("source"), base)
        return bound
    return volume


def audit_compose(
    task: Task, spec: SandboxEnvironmentSpec | None, *, stage: Path
) -> SandboxEnvironmentType:
    """A sandbox holding the audited task's environment and the auditor's, side by side.

    Falls back to the auditor's environment alone when the task brings no compose file,
    which is every task whose sandbox is a bare type or a Dockerfile.

    Args:
        task: The task being audited.
        spec: The sandbox the audited sample runs in, or `None`.
        stage: Directory to write the merged compose and the auditor's Dockerfile into.

    Returns:
        A `("docker", <compose path>)` sandbox spec.
    """
    config = spec.config if spec is not None else None
    # Inspect's rule: a config path is a Dockerfile if named like one, else a compose file.
    if not isinstance(config, str) or is_dockerfile(Path(config).name):
        return audit_sandbox(task)
    source = Path(config)
    if not source.is_file():
        return audit_sandbox(task)

    merged: dict[str, Any] = yaml.safe_load(source.read_text()) or {}
    services: dict[str, Any] = merged.get("services") or {}
    base = source.parent

    benchmark_name = BENCHMARK_SERVICE
    while benchmark_name in services and benchmark_name != "default":
        benchmark_name += "_"
    renamed = {}
    for name, service in services.items():
        anchored = _anchor_service(service, base)
        anchored.pop("x-default", None)
        renamed[benchmark_name if name == "default" else name] = anchored
    for service in renamed.values():
        depends = service.get("depends_on")
        if isinstance(depends, list):
            service["depends_on"] = [benchmark_name if d == "default" else d for d in depends]
        elif isinstance(depends, dict):
            service["depends_on"] = {
                (benchmark_name if k == "default" else k): v for k, v in depends.items()
            }

    stage.mkdir(parents=True, exist_ok=True)
    (stage / "Dockerfile").write_text(
        DOCKERFILE.format(requirements=" ".join(task_requirements(task)))
    )
    auditor = dict(AUDITOR_SERVICE)
    auditor["build"] = {"context": str(stage), "dockerfile": "Dockerfile"}

    merged["services"] = {"default": auditor, **renamed}
    networks = dict(merged.get("networks") or {})
    networks[AUDITOR_NETWORK] = None
    merged["networks"] = networks
    out = stage / "compose.yaml"
    out.write_text(yaml.safe_dump(merged, sort_keys=False))
    return ("docker", str(out))
