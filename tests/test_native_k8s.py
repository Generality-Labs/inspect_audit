"""Native Kubernetes adaptation against the provider's real chart and config types."""

import shutil
import subprocess
from pathlib import Path

import pytest
import yaml
from inspect_ai import Task
from inspect_ai.dataset import Sample
from inspect_ai.util import SandboxEnvironmentSpec
from k8s_sandbox import K8sSandboxEnvironmentConfig

from inspect_audit._compose import UnsupportedBenchmarkError
from inspect_audit._k8s import investigation_k8s
from inspect_audit._task import investigation_task


@pytest.fixture
def values_file(tmp_path: Path) -> Path:
    values = {
        "services": {
            "default": {
                "image": "python:3.12-slim",
                "command": ["sleep", "infinity"],
                "workingDir": "/work",
                "env": [{"name": "MODE", "value": "test"}],
                "resources": {"limits": {"cpu": "1", "memory": "1Gi"}},
                "volumes": [{"name": "scratch", "emptyDir": {}}],
                "volumeMounts": [{"name": "scratch", "mountPath": "/work"}],
                "networks": ["private"],
            },
            "database": {
                "image": "postgres:17",
                "dnsRecord": True,
                "readinessProbe": {"tcpSocket": {"port": 5432}},
                "networks": ["private"],
            },
        },
        "networks": {"private": {}},
        "allowDomains": ["example.org"],
        "allowCIDR": [],
        "allowEntities": [],
        "additionalResources": [
            {"apiVersion": "v1", "kind": "ConfigMap", "metadata": {"name": "fixture"}}
        ],
        "annotations": {"fixture": "preserved"},
    }
    path = tmp_path / "values.yaml"
    path.write_text(yaml.safe_dump(values))
    return path


@pytest.mark.parametrize("object_config", [False, True])
@pytest.mark.parametrize("sample_level", [False, True])
def test_native_values_survive_task_adaptation(
    values_file: Path, object_config: bool, sample_level: bool
) -> None:
    config = (
        K8sSandboxEnvironmentConfig(
            values=values_file, context="fixture-cluster", default_user="root", max_pod_ops=3
        )
        if object_config
        else str(values_file)
    )
    spec = SandboxEnvironmentSpec("k8s", config)
    original = values_file.read_text()
    task = Task(
        dataset=[Sample(id="a", input="q", sandbox=spec if sample_level else None)],
        sandbox=None if sample_level else spec,
    )
    adapted = investigation_task(
        task,
        samples=["a"],
        checks=["gold-answer"],
        judge={},
        source=str(Path(__file__).parent / "fixtures/toy_benchmark/toy.py"),
        investigator_image="investigator:test",
        cost_limit=1,
    )
    assert adapted.sandbox is not None and adapted.sandbox.type == "k8s"
    updated = adapted.sandbox.config
    if object_config:
        assert isinstance(updated, K8sSandboxEnvironmentConfig)
        assert updated.model_dump(exclude={"values"}) == config.model_dump(exclude={"values"})
        path = updated.values
    else:
        path = Path(str(updated))
    assert path is not None
    merged = yaml.safe_load(path.read_text())
    investigator = merged["services"].pop("investigator")
    assert investigator == {"image": "investigator:test", "command": ["tail", "-f", "/dev/null"]}
    assert merged == yaml.safe_load(original)
    assert values_file.read_text() == original
    assert all(sample.sandbox is None for sample in adapted.dataset)


def test_existing_investigator_service_is_refused(values_file: Path, tmp_path: Path) -> None:
    values_file.write_text("services: {investigator: {image: theirs}}\n")
    with pytest.raises(UnsupportedBenchmarkError, match="already has a service"):
        investigation_k8s(
            SandboxEnvironmentSpec("k8s", str(values_file)),
            stage=tmp_path / "stage",
            investigator_image="ours",
        )


@pytest.mark.parametrize("missing", ["image", "file", "chart"])
def test_unsupported_k8s_inputs_fail_at_construction(tmp_path: Path, missing: str) -> None:
    config = K8sSandboxEnvironmentConfig(chart="custom") if missing == "chart" else None
    with pytest.raises(UnsupportedBenchmarkError):
        investigation_k8s(
            SandboxEnvironmentSpec("k8s", config),
            stage=tmp_path,
            investigator_image=None if missing == "image" else "investigator:test",
        )


def test_k8s_compose_keeps_provider_options(tmp_path: Path) -> None:
    source = tmp_path / "compose.yaml"
    source.write_text("services: {default: {image: python:3.12-slim, network_mode: none}}\n")
    spec = investigation_k8s(
        SandboxEnvironmentSpec(
            "k8s", K8sSandboxEnvironmentConfig(values=source, context="fixture-cluster")
        ),
        stage=tmp_path / "stage",
        investigator_image="investigator:test",
    )
    assert isinstance(spec.config, K8sSandboxEnvironmentConfig)
    assert spec.config.context == "fixture-cluster"
    assert spec.config.values is not None
    assert spec.config.values.name == "compose.yaml"
    assert (
        yaml.safe_load(spec.config.values.read_text())["services"]["default"]["network_mode"]
        == "none"
    )


@pytest.mark.skipif(shutil.which("helm") is None, reason="needs the Helm CLI")
def test_native_values_render_with_the_providers_chart(values_file: Path, tmp_path: Path) -> None:
    import k8s_sandbox

    spec = investigation_k8s(
        SandboxEnvironmentSpec("k8s", str(values_file)),
        stage=tmp_path / "stage",
        investigator_image="investigator:test",
    )
    chart = Path(k8s_sandbox.__file__).parent / "resources/helm/agent-env"
    result = subprocess.run(
        ["helm", "template", "fixture", str(chart), "-f", str(spec.config)],
        capture_output=True,
        text=True,
        check=True,
    )
    workloads = {
        document["metadata"]["labels"]["inspect/service"]: document
        for document in yaml.safe_load_all(result.stdout)
        if document and document["kind"] == "StatefulSet"
    }
    assert set(workloads) == {"default", "database", "investigator"}
    default = workloads["default"]["spec"]["template"]["spec"]["containers"][0]
    assert default["workingDir"] == "/work"
    assert default["resources"]["limits"]["memory"] == "1Gi"
    assert {v["name"]: v["value"] for v in default["env"]}["MODE"] == "test"
    assert workloads["database"]["spec"]["template"]["spec"]["containers"][0]["readinessProbe"] == {
        "tcpSocket": {"port": 5432}
    }
