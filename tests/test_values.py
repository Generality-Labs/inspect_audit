"""The k8s emission of the audit sandbox: Helm values instead of a compose file.

No cluster and no network: these check the emitted values file, and -- when a helm
binary and a chart checkout happen to be available -- that the chart renders it.
"""

import os
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml
from inspect_ai import Task
from inspect_ai.dataset import MemoryDataset, Sample
from inspect_ai.scorer import match
from inspect_ai.util import SandboxEnvironmentSpec

from inspect_audit._sandbox import audit_values

AUDITOR_IMAGE = "ghcr.io/example/inspect-audit-auditor:latest"

# The agent-env chart vendored in reference/ (see its VENDORED.md); point
# INSPECT_AUDIT_CHART at a checkout to render against a newer chart instead.
CHART = Path(
    os.environ.get(
        "INSPECT_AUDIT_CHART",
        Path(__file__).parent.parent / "reference" / "agent-env-chart",
    )
)


def make_task() -> Task:
    dataset = MemoryDataset([Sample(input="question", target="answer")])
    return Task(name="fixture_task", dataset=dataset, scorer=match())


def write_compose(tmp_path: Path) -> SandboxEnvironmentSpec:
    compose = tmp_path / "their-compose.yaml"
    compose.write_text(
        "services:\n"
        "  default:\n"
        "    image: python:3.12-slim\n"
        "    working_dir: /workspace\n"
        "    init: true\n"
        "    network_mode: none\n"
        "  db:\n"
        "    image: postgres:16\n"
        "    command: postgres -c fsync=off\n"
        "    runtime: runc\n"
    )
    return SandboxEnvironmentSpec("docker", str(compose))


def emit_values(tmp_path: Path) -> Path:
    sandbox = audit_values(
        make_task(),
        write_compose(tmp_path),
        stage=tmp_path / "stage",
        auditor_image=AUDITOR_IMAGE,
    )
    assert isinstance(sandbox, tuple) and sandbox[0] == "k8s"
    return Path(sandbox[1])


def test_their_services_convert_as_they_ran_on_hawk_and_the_auditor_gets_scoped_egress(
    tmp_path: Path,
) -> None:
    path = emit_values(tmp_path)
    assert path.name == "values.yaml"
    values = yaml.safe_load(path.read_text())

    services = values["services"]
    assert set(services) == {"default", "benchmark", "db"}
    # theirs goes through the chart's own converter, after Hawk's sanitising
    assert services["benchmark"]["image"] == "python:3.12-slim"
    assert services["benchmark"]["workingDir"] == "/workspace"
    assert "init" not in services["benchmark"]
    # network_mode: none stays isolated (it used to be silently dropped)
    assert services["benchmark"]["networkIsolated"] is True
    # a command feeds the image's entrypoint, and the runtime carries over
    assert services["db"]["args"] == ["postgres", "-c", "fsync=off"]
    assert "command" not in services["db"]
    assert services["db"]["runtimeClassName"] == "runc"
    # ours is the published auditor image, and every service gets a DNS record
    assert services["default"]["image"] == AUDITOR_IMAGE
    assert services["default"]["command"] == ["sleep", "infinity"]
    assert all(service["dnsRecord"] is True for service in services.values())

    # egress is a policy scoped to the auditor's pod, never a sandbox-wide grant
    for key in ("allowDomains", "allowEntities", "allowCIDR"):
        assert key not in values
    policies = values["additionalResources"]
    assert len(policies) == 1
    assert "CiliumNetworkPolicy" in policies[0]
    assert "inspect/service: default" in policies[0]


def test_a_built_service_requires_a_published_image(tmp_path: Path) -> None:
    compose = tmp_path / "their-compose.yaml"
    compose.write_text("services:\n  default:\n    build: .\n")
    spec = SandboxEnvironmentSpec("docker", str(compose))

    with pytest.raises(ValueError, match="'default'"):
        audit_values(make_task(), spec, stage=tmp_path / "stage", auditor_image=AUDITOR_IMAGE)

    sandbox = audit_values(
        make_task(),
        spec,
        stage=tmp_path / "stage",
        auditor_image=AUDITOR_IMAGE,
        benchmark_image="ghcr.io/example/benchmark:latest",
    )
    values = yaml.safe_load(Path(str(sandbox[1])).read_text())
    assert values["services"]["benchmark"]["image"] == "ghcr.io/example/benchmark:latest"
    assert "build" not in values["services"]["benchmark"]


def test_a_stand_in_images_command_replaces_its_entrypoint(tmp_path: Path) -> None:
    """The stand-in's entrypoint is not the build's; its command must run as given."""
    compose = tmp_path / "their-compose.yaml"
    compose.write_text("services:\n  default:\n    build: .\n    command: sleep infinity\n")
    sandbox = audit_values(
        make_task(),
        SandboxEnvironmentSpec("docker", str(compose)),
        stage=tmp_path / "stage",
        auditor_image=AUDITOR_IMAGE,
        benchmark_image="ghcr.io/example/benchmark:latest",
    )
    values = yaml.safe_load(Path(str(sandbox[1])).read_text())
    assert values["services"]["benchmark"]["command"] == ["sleep", "infinity"]
    assert "args" not in values["services"]["benchmark"]


def test_bridge_networking_gets_the_world_egress_hawk_gives_it(tmp_path: Path) -> None:
    compose = tmp_path / "their-compose.yaml"
    compose.write_text("services:\n  default:\n    image: nginx\n    network_mode: bridge\n")
    sandbox = audit_values(
        make_task(),
        SandboxEnvironmentSpec("docker", str(compose)),
        stage=tmp_path / "stage",
        auditor_image=AUDITOR_IMAGE,
    )
    values = yaml.safe_load(Path(str(sandbox[1])).read_text())
    assert "world" in values["allowEntities"]
    assert "network_mode" not in values["services"]["benchmark"]


def test_without_a_compose_file_the_auditor_stands_alone(tmp_path: Path) -> None:
    sandbox = audit_values(make_task(), None, stage=tmp_path, auditor_image=AUDITOR_IMAGE)
    values = yaml.safe_load(Path(str(sandbox[1])).read_text())
    assert set(values["services"]) == {"default"}
    assert "additionalResources" in values


@pytest.mark.skipif(
    shutil.which("helm") is None or not CHART.is_dir(),
    reason="needs the helm binary and the agent-env chart checkout",
)
def test_helm_renders_the_generated_values(tmp_path: Path) -> None:
    path = emit_values(tmp_path)
    rendered = subprocess.run(
        ["helm", "template", "audit-values-test", str(CHART), "--values", str(path)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert rendered.returncode == 0, rendered.stderr
    assert "inspect/service: default" in rendered.stdout
