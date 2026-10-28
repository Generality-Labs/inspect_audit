"""The investigation's compose is merged in Python; Docker's own merge is the reference.

The runner on Hawk has no docker, so `docker compose config` cannot flatten the
benchmark's compose there. Ours must resolve the same host paths Docker does, and
leave everything else as their file wrote it.
"""

import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

from inspect_audit._compose import INVESTIGATOR, UnsupportedBenchmarkError, investigation_compose

THEIRS = """
services:
  default:
    build:
      context: ./box
      dockerfile: Dockerfile
    env_file: ./box/env
    volumes:
      - ./data:/data:ro
      - cache:/cache
      - type: bind
        source: ./more
        target: /more
    network_mode: none
  helper:
    build: ./helper
    command: sleep infinity
volumes:
  cache: {}
configs:
  settings:
    file: ./settings.json
"""


def _benchmark(tmp_path: Path) -> Path:
    root = tmp_path / "benchmark"
    for directory in ("box", "helper", "data", "more"):
        (root / directory).mkdir(parents=True)
    (root / "box" / "Dockerfile").write_text("FROM python:3.12-slim\n")
    (root / "helper" / "Dockerfile").write_text("FROM python:3.12-slim\n")
    (root / "box" / "env").write_text("A=1\n")
    (root / "settings.json").write_text("{}")
    (root / "compose.yaml").write_text(THEIRS)
    return root / "compose.yaml"


def test_their_paths_resolve_against_their_folder_and_the_rest_is_untouched(
    tmp_path: Path,
) -> None:
    compose = _benchmark(tmp_path)
    spec = investigation_compose(
        compose, stage=tmp_path / "stage", investigator_image="investigator:latest"
    )
    merged = yaml.safe_load(Path(spec.config).read_text())  # type: ignore[arg-type]
    root = compose.parent.resolve()
    default = merged["services"]["default"]
    assert default["build"] == {"context": str(root / "box"), "dockerfile": "Dockerfile"}
    assert default["env_file"] == str(root / "box" / "env")
    assert default["volumes"][0] == f"{root / 'data'}:/data:ro"
    assert default["volumes"][1] == "cache:/cache"
    assert default["volumes"][2]["source"] == str(root / "more")
    assert default["network_mode"] == "none"
    assert merged["services"]["helper"]["build"] == str(root / "helper")
    assert merged["configs"]["settings"]["file"] == str(root / "settings.json")
    assert merged["volumes"] == {"cache": {}}
    assert merged["services"][INVESTIGATOR]["network_mode"] == "bridge"


def test_an_include_or_extends_is_refused_not_half_merged(tmp_path: Path) -> None:
    included = tmp_path / "included.yaml"
    included.write_text("include: [other.yaml]\nservices: {default: {image: x}}\n")
    with pytest.raises(UnsupportedBenchmarkError, match="include"):
        investigation_compose(included, stage=tmp_path / "a")
    extended = tmp_path / "extended.yaml"
    extended.write_text("services: {default: {extends: {file: base.yaml, service: b}}}\n")
    with pytest.raises(UnsupportedBenchmarkError, match="extends"):
        investigation_compose(extended, stage=tmp_path / "b")


@pytest.mark.skipif(shutil.which("docker") is None, reason="needs the docker CLI")
def test_the_merge_resolves_paths_as_docker_does(tmp_path: Path) -> None:
    """Docker's `compose config` is the reference for every path it resolves."""
    compose = _benchmark(tmp_path)
    spec = investigation_compose(
        compose, stage=tmp_path / "stage", investigator_image="investigator:latest"
    )
    ours = yaml.safe_load(Path(spec.config).read_text())  # type: ignore[arg-type]
    docker = yaml.safe_load(
        subprocess.run(
            ["docker", "compose", "-f", str(compose), "config"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout
    )
    for name in ("default", "helper"):
        assert Path(
            ours["services"][name]["build"]["context"]
            if isinstance(ours["services"][name]["build"], dict)
            else ours["services"][name]["build"]
        ) == Path(docker["services"][name]["build"]["context"])
    binds = [v for v in docker["services"]["default"]["volumes"] if v["type"] == "bind"]
    ours_sources = {
        v.split(":")[0] if isinstance(v, str) else v["source"]
        for v in ours["services"]["default"]["volumes"]
    }
    assert {b["source"] for b in binds} <= ours_sources
    # Docker inlines env_file into `environment`; ours keeps the (absolute) file, which
    # the Docker provider reads at run time. The k8s converter refuses both env_file
    # and host binds, so a benchmark that runs on Hawk has neither.
