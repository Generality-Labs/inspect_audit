"""`reset`: rebuild the benchmark's boxes from their images, mid-sample.

Inspect sets a sample's boxes up once and offers no public way to do it again, so
this is the one module allowed Inspect's private internals: the Docker project
handle, and the same routines sample-init uses to lay files and run setup -- reused,
not reimplemented, so a rebuilt box starts the way the first one did. Docker only;
on any other provider it refuses.
"""

from copy import copy

from inspect_ai._eval.task.sandbox import read_sandboxenv_file, resolve_sample_files
from inspect_ai.dataset import Sample
from inspect_ai.solver import Generate, Solver, TaskState
from inspect_ai.tool import Tool, ToolError, tool
from inspect_ai.util import SandboxEnvironment, sandbox
from inspect_ai.util import subprocess as async_subprocess
from inspect_ai.util._sandbox.context import (
    copy_sandbox_environment_files,
    setup_sandbox_environment,
)
from inspect_ai.util._sandbox.docker.docker import DockerSandboxEnvironment

from ._compose import AUDITOR

RECREATE_TIMEOUT = 900


async def rebuild(
    their_sample: Sample, setup_solvers: list[Solver], state: TaskState, generate: Generate
) -> list[str]:
    """Force-recreate every benchmark service, then lay files and run setup again."""
    try:
        docker = sandbox().as_type(DockerSandboxEnvironment)
    except TypeError:
        raise ToolError("reset rebuilds Docker boxes; this run's sandbox is not Docker") from None
    project = docker._project  # pyright: ignore[reportPrivateUsage]
    if project.config is None:
        raise ToolError("reset needs the compose file this sample's boxes were started from")

    base = ["docker", "compose", "-p", project.name, "-f", project.config]
    listed = await async_subprocess([*base, "config", "--services"], timeout=120)
    if not listed.success:
        raise ToolError(f"could not list the benchmark's services: {listed.stderr.strip()}")
    services = [s for s in listed.stdout.split() if s and s != AUDITOR]
    recreated = await async_subprocess(
        [*base, "up", "-d", "--force-recreate", "--no-deps", "--wait", *services],
        timeout=RECREATE_TIMEOUT,
    )
    if not recreated.success:
        raise ToolError(
            "the benchmark's boxes did not come back after a rebuild -- a fault beyond "
            f"the containers (daemon, disk, shared resource): {recreated.stderr.strip()[-2000:]}"
        )

    # the default first: Inspect's helpers send an unprefixed file to the default
    ordered = ["default", *(s for s in services if s != "default")]
    environments: dict[str, SandboxEnvironment] = {name: sandbox(name) for name in ordered}
    if their_sample.files:
        resolved = resolve_sample_files(their_sample.files)
        files = {path: await read_sandboxenv_file(contents) for path, contents in resolved.items()}
        await copy_sandbox_environment_files(files, environments)
    if their_sample.setup:
        script = (await read_sandboxenv_file(their_sample.setup)).decode("utf-8")
        if not script.strip().startswith("#!"):
            script = f"#!/usr/bin/env bash\n\n{script}"
        await setup_sandbox_environment(script.encode("utf-8"), environments)
    # their setup solvers may prepare the box too; run them on a copy so whatever
    # they do to the conversation stays off the audit's state
    for solver in setup_solvers:
        await solver(copy(state), generate)
    return services


def reset_tool(
    their_sample: Sample, setup_solvers: list[Solver], state: TaskState, generate: Generate
) -> Tool:
    """`reset` bound to one audited sample."""

    @tool
    def reset() -> Tool:
        async def execute() -> str:
            """Rebuild the benchmark's boxes from their images and set them up again.

            Recreates every benchmark container (yours is untouched), copies the
            sample's files back in, re-runs its setup script and the benchmark's
            setup solvers: the boxes start exactly as they did for the evaluated
            agent. Anything you changed in them is gone.
            """
            services = await rebuild(their_sample, setup_solvers, state, generate)
            return f"rebuilt {', '.join(services)}: files laid, setup run"

        return execute

    return reset()
