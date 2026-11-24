"""Questions about the benchmark that need its code, answered in the auditor's box.

The audit's own process holds the operator's credentials (on Hawk, under the temporary
user mode, the operator's login). Importing a benchmark runs its code, and building its
task may download its dataset, so neither happens here: a small script runs in the box,
where the benchmark is installed (`install_benchmark`) and no credentials are held, and
hands back JSON. The scripts import only Inspect and the benchmark, which the box has.
"""

import json
from pathlib import PurePosixPath
from typing import Any

from inspect_ai.tool import ToolError
from inspect_ai.util import sandbox

WORK = "/tmp/inspect-audit-probe"

# The task's samples with the ids an eval gives them (`_task.numbered`'s rule: id-less
# samples are numbered by position + 1, over the whole dataset).
POPULATION = r"""
import importlib, json, sys
from inspect_ai.util import registry_create

request = json.load(open(sys.argv[1]))
if request["source"]:
    # registers the task where it has no entry point
    importlib.import_module(request["source"])
task = registry_create("task", request["task"], **request["task_args"])
ids = [sample.id for sample in task.dataset]
missing = sum(i is None for i in ids)
if missing and missing != len(ids):
    raise SystemExit("some of the benchmark's samples have ids and some do not")
# models the task loads by default, from parameters the audit's arguments leave unset
# (writingbench's `judge_model`): a job is given credentials only for models it names
import inspect, re
from inspect_ai._util.registry import registry_lookup

function = inspect.unwrap(registry_lookup("task", request["task"]))
defaults = {
    name: param.default
    for name, param in inspect.signature(function).parameters.items()
    if re.search(r"(^|_)(model|judge|grader)s?($|_)", name, re.IGNORECASE)
    and isinstance(param.default, str)
    # a provider and a name ("anthropic/claude-x"), not any text with a slash ("n/a")
    and re.fullmatch(r"[A-Za-z][\w.-]+/[\w./:@-]+", param.default)
    and name not in request["task_args"]
}
json.dump(
    {
        "ids": [str(n + 1) if missing else str(i) for n, i in enumerate(ids)],
        "positional": bool(missing),
        "dataset": getattr(task.dataset, "name", None),
        "model_defaults": defaults,
    },
    open(sys.argv[2], "w"),
)
"""

# Where the module defining the task is installed, after checking it defines that task
# (`_variant.task_package`'s rule).
PACKAGE = """
import importlib, importlib.util, json, sys
from inspect_ai.util import registry_info

request = json.load(open(sys.argv[1]))
source, task = request["source"], request["task"]
function = task.split("/")[-1]
registered = registry_info(getattr(importlib.import_module(source), function)).name
if registered.split("/")[-1] != function:
    raise SystemExit(f"{source}.{function} registers as {registered!r}, not {task!r}")
top = importlib.util.find_spec(source.split(".")[0])
locations = list(top.submodule_search_locations or []) if top else []
if not locations:
    raise SystemExit(f"{source!r} is not a module inside an installed package")
json.dump({"package": locations[0]}, open(sys.argv[2], "w"))
"""


async def run_probe(
    name: str, script: str, request: dict[str, Any], env: dict[str, str] | None = None
) -> Any:
    """Run `script` in the box with `request` (and `env`); its JSON answer, or a tool error
    with why not."""
    box = sandbox()
    await box.write_file(f"{WORK}/{name}.py", script)
    await box.write_file(f"{WORK}/{name}.request.json", json.dumps(request))
    answer = f"{WORK}/{name}.answer.json"
    result = await box.exec(
        ["python", f"{WORK}/{name}.py", f"{WORK}/{name}.request.json", answer],
        env=env or {},
        timeout=1800,
    )
    if not result.success:
        killed = (
            # SIGKILL: on a pod, the kernel's out-of-memory killer (breadth round, docvqa:
            # 5,349 images built into a dataset)
            "\nThe box most likely ran out of memory. The operator can give it more with "
            'box_resources, e.g. {"limits": {"memory": "16Gi"}}.'
            if result.returncode in (137, -9)
            else ""
        )
        raise ToolError(
            f"reading the benchmark in the box failed ({name}, exit {result.returncode}):\n"
            + why_it_failed(result.stderr or result.stdout)
            + killed
        )
    return json.loads(await box.read_file(answer))


def why_it_failed(output: str, limit: int = 3000) -> str:
    """The part of a failed script's output that says why.

    Its last traceback, else its last lines, with progress bars (redrawn with carriage
    returns) reduced to their final state. Breadth round, docvqa: the tail was 3000
    characters of one progress bar, and the failure itself was cut off.
    """
    lines = [line.rsplit("\r", 1)[-1] for line in output.splitlines()]
    text = "\n".join(line for line in lines if line.strip())
    start = text.rfind("Traceback (most recent call last)")
    return (text[start:] if start != -1 else text)[-limit:]


async def population(
    task: str,
    task_args: dict[str, Any],
    source: str | None = None,
    environment: dict[str, str] | None = None,
) -> dict[str, Any]:
    """The benchmark's sample ids, whether they are positions, and its dataset's name.

    `environment` is the audit's benchmark_environment: building the task may need it
    (ExploitBench refuses to load without EXPLOITBENCH_ACKNOWLEDGE_RISKS=1), and the box
    does not inherit the runner's environment."""
    return await run_probe(
        "population",
        POPULATION,
        {"task": task, "task_args": task_args, "source": source},
        env=environment,
    )


async def installed_package(task: str, source: str) -> PurePosixPath:
    """The folder of the installed top-level package that defines `task`, in the box."""
    answer = await run_probe("package", PACKAGE, {"task": task, "source": source})
    return PurePosixPath(answer["package"])
