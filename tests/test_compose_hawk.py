"""The investigation's compose against Hawk's own sandbox rules and Inspect's k8s converter.

Hawk's runner sanitises every compose before converting it (run_eval_set.py
`_patch_network_mode`); that module does not import outside a runner, so its
functions are lifted from the installed source and run as shipped. The corpus is
every compose file in the installed inspect_evals: any benchmark Hawk accepts on
its own must still be accepted with the investigator added.
"""

import ast
import copy
import logging
from pathlib import Path
from typing import Any, cast

import pytest
import yaml

from inspect_audit._compose import investigation_compose

SANITISER = (
    "_compose_services",
    "_service_networks",
    "_is_external_network",
    "_patch_network_mode",
)


def _hawk_sanitiser() -> Any:
    import hawk.runner

    source = (Path(hawk.runner.__file__).parent / "run_eval_set.py").read_text()
    tree = ast.parse(source)
    functions = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in SANITISER]
    assert {f.name for f in functions} == set(SANITISER), "hawk moved its sanitiser"
    namespace: dict[str, Any] = {"Any": Any, "cast": cast, "logger": logging.getLogger("hawk")}
    exec(compile(ast.Module(body=functions, type_ignores=[]), "run_eval_set.py", "exec"), namespace)
    return namespace["_patch_network_mode"]


def _accepted(compose: dict[str, Any], where: Path) -> str | None:
    """None if Hawk's sanitiser and the k8s converter both accept it, else why not."""
    from k8s_sandbox.compose import convert_compose_to_helm_values

    sanitise = _hawk_sanitiser()
    compose = copy.deepcopy(compose)
    try:
        sanitise(compose)
        path = where / "sanitised.yaml"
        path.write_text(yaml.safe_dump(compose))
        convert_compose_to_helm_values(path)
    except Exception as ex:
        return f"{type(ex).__name__}: {str(ex)[:200]}"
    return None


def _corpus() -> list[Path]:
    import inspect_evals

    root = Path(inspect_evals.__file__).parent
    return sorted(p for p in root.rglob("*.yaml") if "compose" in p.name)


def test_every_benchmark_hawk_accepts_is_still_accepted_with_the_investigator(
    tmp_path: Path,
) -> None:
    files = _corpus()
    if not files:
        pytest.skip("inspect_evals has no compose files installed")
    lost: list[str] = []
    accepted_alone = 0
    for index, file in enumerate(files):
        theirs = yaml.safe_load(file.read_text()) or {}
        if not isinstance(theirs, dict) or not theirs.get("services"):
            continue
        work = tmp_path / str(index)
        work.mkdir()
        if _accepted(theirs, work) is not None:
            continue  # Hawk refuses the benchmark itself; nothing for us to keep
        accepted_alone += 1
        spec = investigation_compose(
            file, stage=work / "stage", investigator_image="investigator:latest"
        )
        merged = yaml.safe_load(Path(cast(str, spec.config)).read_text())
        if (why := _accepted(merged, work)) is not None:
            # accepted (2026-10-20): Hawk grants internet per sandbox, so a benchmark
            # that keeps its services on an internal network cannot be investigated there
            if "network marked 'internal: true'" in why:
                continue
            lost.append(f"{file.relative_to(file.parents[2])}: {why}")
    assert accepted_alone > 0
    assert not lost, f"{len(lost)} of {accepted_alone} lost:\n" + "\n".join(lost[:15])
