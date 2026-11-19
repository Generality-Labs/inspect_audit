"""Egress probe: can the Hawk runner and a k8s box reach web-search APIs? No model calls."""

import json
import tempfile
from pathlib import Path

import yaml
from inspect_ai import Task, task
from inspect_ai.dataset import Sample
from inspect_ai.scorer import Score, Target, scorer, mean
from inspect_ai.solver import Generate, TaskState, solver
from inspect_ai.util import sandbox

from .containers import INVESTIGATOR_IMAGE

TARGETS = {
    "tavily": ("POST", "https://api.tavily.com/search", {"query": "test"}),
    "exa": ("POST", "https://api.exa.ai/search", {"query": "test"}),
    "google": ("GET", "https://www.googleapis.com/customsearch/v1?q=test", None),
}


@solver
def probe():
    async def solve(state: TaskState, generate: Generate) -> TaskState:
        import httpx

        results: dict = {"runner": {}, "sandbox": {}}
        async with httpx.AsyncClient(timeout=20) as client:
            for name, (method, url, body) in TARGETS.items():
                try:
                    r = await client.request(method, url, json=body)
                    results["runner"][name] = {"status": r.status_code, "body": r.text[:200]}
                except Exception as ex:
                    results["runner"][name] = {"error": f"{type(ex).__name__}: {ex}"[:300]}
        for name, (method, url, body) in TARGETS.items():
            cmd = ["curl", "-sS", "-m", "20", "-o", "-", "-w", "\nHTTP=%{http_code}", "-X", method, url]
            if body is not None:
                cmd += ["-H", "Content-Type: application/json", "-d", json.dumps(body)]
            try:
                r = await sandbox().exec(cmd, timeout=40)
                results["sandbox"][name] = {
                    "rc": r.returncode,
                    "stdout": r.stdout[-300:],
                    "stderr": r.stderr[-300:],
                }
            except Exception as ex:
                results["sandbox"][name] = {"error": f"{type(ex).__name__}: {ex}"[:300]}
        state.metadata["egress"] = results
        state.output.completion = json.dumps(results, indent=1)
        return state

    return solve


@scorer(metrics=[mean()])
def recorded():
    async def score(state: TaskState, target: Target) -> Score:
        return Score(value=1, explanation=json.dumps(state.metadata.get("egress")))

    return score


@task
def egress_probe() -> Task:
    root = Path(tempfile.mkdtemp())
    values = {
        "services": {
            "default": {
                "image": INVESTIGATOR_IMAGE,
                "command": ["sleep", "infinity"],
            }
        },
        "allowEntities": ["world"],
    }
    (root / "probe.values.yaml").write_text(yaml.safe_dump(values))
    return Task(
        dataset=[Sample(input="probe", id="probe")],
        solver=probe(),
        scorer=recorded(),
        sandbox=("k8s", str(root / "probe.values.yaml")),
    )
