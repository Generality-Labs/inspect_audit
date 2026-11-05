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
    assert "x-default" not in merged["services"][INVESTIGATOR]


def _default_of(tmp_path: Path, text: str) -> str:
    compose = tmp_path / "compose.yaml"
    compose.write_text(text)
    spec = investigation_compose(
        compose, stage=tmp_path / "stage", investigator_image="investigator:latest"
    )
    services = yaml.safe_load(Path(spec.config).read_text())["services"]  # type: ignore[arg-type]
    assert "x-default" not in services[INVESTIGATOR]
    defaults = [n for n, s in services.items() if n == "default" or s.get("x-default")]
    assert len(defaults) == 1
    return defaults[0]


def test_the_benchmarks_default_is_found_by_the_providers_own_rules(tmp_path: Path) -> None:
    assert (
        _default_of(tmp_path, "services: {target: {image: t}, default: {image: d}}\n") == "default"
    )
    assert (
        _default_of(
            tmp_path, "services: {target: {image: t}, agent: {image: a, x-default: true}}\n"
        )
        == "agent"
    )
    # no marker: only Kubernetes could have run it, and it takes the first service
    assert (
        _default_of(tmp_path, "services: {attacker: {image: a}, target: {image: t}}\n")
        == "attacker"
    )


def test_a_default_that_cannot_be_known_is_refused(tmp_path: Path) -> None:
    (tmp_path / "more.yaml").write_text("services: {target: {image: t}}\n")
    compose = tmp_path / "compose.yaml"
    compose.write_text("include: [more.yaml]\nservices: {agent: {image: a}}\n")
    with pytest.raises(UnsupportedBenchmarkError, match="cannot be known"):
        investigation_compose(compose, stage=tmp_path / "a")
    compose.write_text(
        "services: {a: {image: a, x-default: true}, b: {image: b, x-default: true}}\n"
    )
    with pytest.raises(UnsupportedBenchmarkError, match="several"):
        investigation_compose(compose, stage=tmp_path / "b")


LAYERED = {
    "compose.yaml": """
include:
  - shared/services.yaml
services:
  default:
    extends: {file: shared/base.yaml, service: kali}
    environment: {ROLE: attacker}
    volumes: [./work:/work]
  target:
    extends: web
    command: serve --port 80
  web:
    image: web:${WEB_TAG:-latest}
    environment: [MODE=prod, DEBUG=0]
    volumes: [./site:/site]
""",
    "shared/base.yaml": """
services:
  kali:
    image: kali-${KALI_SIZE:-large}
    build: ./kali
    environment: {DEBUG: "1", ROLE: base}
    volumes: [./tools:/tools, ./old:/work]
    depends_on: [db]
""",
    "shared/services.yaml": """
services:
  db:
    image: postgres
    volumes: [./initdb:/docker-entrypoint-initdb.d]
volumes:
  dbdata: {}
""",
}


def _layered(tmp_path: Path) -> Path:
    root = tmp_path / "benchmark"
    for name, text in LAYERED.items():
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_text(text)
    for directory in ("shared/kali", "shared/tools", "shared/initdb", "work", "site"):
        (root / directory).mkdir(parents=True, exist_ok=True)
    (root / "shared/kali/Dockerfile").write_text("FROM scratch\n")
    return root / "compose.yaml"


def test_includes_and_extends_are_resolved_each_against_its_own_folder(tmp_path: Path) -> None:
    compose = _layered(tmp_path)
    spec = investigation_compose(
        compose, stage=tmp_path / "stage", investigator_image="investigator:latest"
    )
    merged = yaml.safe_load(Path(spec.config).read_text())  # type: ignore[arg-type]
    root = compose.parent.resolve()
    services = merged["services"]
    assert "include" not in merged
    assert set(services) == {"default", "target", "web", "db", INVESTIGATOR}
    assert merged["volumes"] == {"dbdata": {}}
    assert services["db"]["volumes"] == [f"{root / 'shared/initdb'}:/docker-entrypoint-initdb.d"]

    default = services["default"]
    assert "extends" not in default
    # the base's per-sample variable is left for Docker to fill in at run time
    assert default["image"] == "kali-${KALI_SIZE:-large}"
    assert default["build"] == str(root / "shared/kali")
    assert default["environment"] == {"DEBUG": "1", "ROLE": "attacker"}
    # a mount on the same target is replaced; the base's other mounts keep their folder
    assert default["volumes"] == [f"{root / 'shared/tools'}:/tools", f"{root / 'work'}:/work"]
    assert "depends_on" not in default

    target = services["target"]
    assert target["image"] == "web:${WEB_TAG:-latest}"
    assert target["command"] == "serve --port 80"
    # inherited unchanged when the service adds none of its own
    assert target["environment"] == ["MODE=prod", "DEBUG=0"]
    assert target["volumes"] == [f"{root / 'site'}:/site"]


def test_compose_files_that_cannot_be_merged_are_refused(tmp_path: Path) -> None:
    missing = tmp_path / "missing.yaml"
    missing.write_text("include: [other.yaml]\nservices: {default: {image: x}}\n")
    with pytest.raises(UnsupportedBenchmarkError, match="not found"):
        investigation_compose(missing, stage=tmp_path / "a")
    (tmp_path / "twice.yaml").write_text("services: {default: {image: y}}\n")
    clash = tmp_path / "clash.yaml"
    clash.write_text("include: [twice.yaml]\nservices: {default: {image: x}}\n")
    with pytest.raises(UnsupportedBenchmarkError, match="already has"):
        investigation_compose(clash, stage=tmp_path / "b")
    loop = tmp_path / "loop.yaml"
    loop.write_text("include: [loop.yaml]\nservices: {default: {image: x}}\n")
    with pytest.raises(UnsupportedBenchmarkError, match="loop"):
        investigation_compose(loop, stage=tmp_path / "c")
    unknown = tmp_path / "unknown.yaml"
    unknown.write_text("services: {default: {extends: nowhere}}\n")
    with pytest.raises(UnsupportedBenchmarkError, match="nowhere"):
        investigation_compose(unknown, stage=tmp_path / "d")


def _env(service: dict[str, object]) -> dict[str, str]:
    """A service's environment as a mapping, whichever form compose wrote it in."""
    env = service.get("environment") or {}
    if isinstance(env, list):
        return {k: v for k, _, v in (str(item).partition("=") for item in env)}
    return {str(k): str(v) for k, v in dict(env).items()}  # type: ignore[arg-type]


@pytest.mark.skipif(shutil.which("docker") is None, reason="needs the docker CLI")
def test_includes_and_extends_merge_as_docker_does(tmp_path: Path) -> None:
    """`docker compose config` (parsing only, nothing pulled or run) is the reference."""
    compose = _layered(tmp_path)
    spec = investigation_compose(
        compose, stage=tmp_path / "stage", investigator_image="investigator:latest"
    )
    ours = yaml.safe_load(Path(spec.config).read_text())["services"]  # type: ignore[arg-type]
    docker = yaml.safe_load(
        subprocess.run(
            ["docker", "compose", "-f", str(compose), "config", "--no-interpolate"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout
    )["services"]
    for name in ("default", "target", "web", "db"):
        assert ours[name].get("image") == docker[name].get("image")
        assert _env(ours[name]) == _env(docker[name])
        assert sorted(
            (v.split(":")[0], v.split(":")[1]) for v in ours[name].get("volumes", [])
        ) == sorted((v["source"], v["target"]) for v in docker[name].get("volumes", []))
    assert Path(ours["default"]["build"]) == Path(docker["default"]["build"]["context"])
    assert ours["target"]["command"] == "serve --port 80"


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
