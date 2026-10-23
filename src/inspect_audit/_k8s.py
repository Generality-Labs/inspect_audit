"""Add an investigator service to the built-in Inspect Kubernetes chart's values."""

from pathlib import Path

import yaml
from inspect_ai.util import ComposeConfig, SandboxEnvironmentSpec

from ._compose import INVESTIGATOR, UnsupportedBenchmarkError, investigation_compose, their_compose


def investigation_k8s(
    spec: SandboxEnvironmentSpec, *, stage: Path, investigator_image: str | None
) -> SandboxEnvironmentSpec:
    """Preserve native Helm values and provider options; add the investigator's image.

    Kubernetes declarations may also name Compose files. Those use the existing
    Compose adapter, leaving conversion to the provider. Native values keep their
    network policy, including any restrictions on the investigator's internet access.
    """
    from k8s_sandbox import K8sSandboxEnvironmentConfig
    from k8s_sandbox.compose import is_docker_compose_file

    if not investigator_image:
        raise UnsupportedBenchmarkError(
            "a k8s investigation requires a published investigator_image"
        )
    stage.mkdir(parents=True, exist_ok=True)
    config = spec.config
    options = config if isinstance(config, K8sSandboxEnvironmentConfig) else None
    if options is not None:
        if options.chart is not None:
            raise UnsupportedBenchmarkError(
                "k8s investigations support the built-in chart, not custom charts"
            )
        source = options.values
    elif isinstance(config, str):
        source = Path(config)
    elif isinstance(config, ComposeConfig):
        source = their_compose(spec, None, stage)
    else:
        raise UnsupportedBenchmarkError(
            "a k8s investigation requires an explicit Helm values or Compose file"
        )
    if source is None or not source.is_file():
        raise UnsupportedBenchmarkError(f"k8s sandbox config file not found: {source}")
    if is_docker_compose_file(source):
        merged = investigation_compose(source, stage=stage, investigator_image=investigator_image)
        path = Path(str(merged.config))
    else:
        values = yaml.safe_load(source.read_text())
        if not isinstance(values, dict):
            raise UnsupportedBenchmarkError("k8s Helm values must be a mapping")
        services = values.setdefault("services", {})
        if not isinstance(services, dict):
            raise UnsupportedBenchmarkError("k8s services must be a mapping")
        if INVESTIGATOR in services:
            raise UnsupportedBenchmarkError(
                f"the benchmark already has a service named {INVESTIGATOR!r}"
            )
        services[INVESTIGATOR] = {
            "image": investigator_image,
            "command": ["tail", "-f", "/dev/null"],
        }
        path = stage / "investigation.values.yaml"
        path.write_text(yaml.safe_dump(values, sort_keys=False))
    updated = (
        options.model_copy(update={"values": path.resolve()}) if options else str(path.resolve())
    )
    return SandboxEnvironmentSpec("k8s", updated)
