"""Publishing packages and phase-dependent tools, using Inspect's ToolSource API."""

import importlib
import shutil
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from inspect_ai import Task, task
from inspect_ai.agent import AgentState, react
from inspect_ai.dataset import Sample
from inspect_ai.solver import Generate, Solver, TaskState, solver
from inspect_ai.tool import Tool, ToolDef, ToolError, bash, skill, tool
from inspect_ai.util import CheckpointConfig, store_as
from pydantic import TypeAdapter

from ._findings import FindingsState, encoded, read_bundle, restore_bundle
from ._image import view_image
from ._report import InvestigationState, investigation_outcome, report_blocker


@dataclass(frozen=True)
class Publisher:
    """A trusted publishing package: skills, optional tools and an output builder.

    A factory ``module:factory`` returns this object. ``build(root, bundle)`` reads draft output
    from the sandbox and returns a host directory of finished artifacts. The
    second argument is the trusted submitted bundle, independent of the workspace. It must
    not edit the submitted bundle. No benchmark installation is required.
    """

    skills: list[str]
    build: Callable[[Path, Path], Awaitable[Path]]
    tools: Callable[[Path], list[Tool]] = lambda root: []


def load_publisher(name: str) -> Publisher:
    if name == "gl":
        from .publishers.gl import publisher

        return publisher()
    module, separator, factory = name.partition(":")
    if not separator:
        raise ValueError("publisher must be 'gl' or an installed module:factory")
    result = getattr(importlib.import_module(module), factory)()
    if not isinstance(result, Publisher):
        raise TypeError("Publisher factories must return inspect_audit.Publisher")
    return result


@tool(parallel=False)
def publish_document(root: Path, publisher: Publisher) -> Tool:
    async def execute() -> str:
        """Build the report from final findings and preserve its output bundle."""
        from ._investigation_workspace import persist

        bundle = await restore_bundle(root)
        try:
            output = await publisher.build(root, bundle)
        except (ValueError, OSError, ImportError) as ex:
            raise ToolError(f"Publication failed: {ex}") from ex
        if output.is_symlink() or any(
            p.is_symlink() or p.suffix == ".eval" for p in output.rglob("*")
        ):
            raise ToolError("Publisher output must contain regular files, without native eval logs")
        destination = root / "published" / uuid4().hex
        destination.parent.mkdir(exist_ok=True)
        shutil.copytree(output, destination)
        shutil.copytree(bundle, destination / "findings")
        (destination / "publication.json").write_bytes(
            encoded(
                {
                    "findings_sha256": store_as(FindingsState).manifest["files"]["findings.json"][
                        "sha256"
                    ]
                }
            )
        )
        location = str(destination)
        if (root / "artifacts.json").exists():
            location = await persist(root, destination, f"published/{destination.name}")
        state = store_as(InvestigationState)
        state.published = location
        # Submission preserves missing work explicitly. A report does not turn it into coverage.
        from ._findings import Findings
        from ._register import Register, apply_reviews, select
        from ._verdicts import AuditItemSkill

        findings = Findings.model_validate(store_as(FindingsState).findings)
        checks = TypeAdapter(dict[str, AuditItemSkill]).validate_python(findings.auditors["checks"])
        register = Register.model_validate(
            {"checks": list(checks), "samples": findings.auditors["samples"]}
        )
        apply_reviews(register, findings.investigator.reviews, checks)
        unresolved = select(register, "missing", checks, None) or select(
            register, "unresolved", checks, None
        )
        state.outcome = "published_incomplete" if unresolved else "published_complete"
        state.outcome_reasons = (
            ["Some planned sample/check cells are missing or unresolved"]
            if state.outcome == "published_incomplete"
            else []
        )
        required = store_as(FindingsState).manifest.get("required_coverage")
        if required is not None:
            from ._report import assessed_fraction

            relative = (
                store_as(FindingsState)
                .manifest.get("artifacts", {})
                .get("/workspace/analysis/coverage.json")
            )
            measured = assessed_fraction(bundle / relative) if relative else None
            if measured is None or measured[0] < required:
                state.outcome = "published_incomplete"
                state.outcome_reasons = [
                    f"Required coverage {required:.0%} was not established by the submitted evidence"
                ]
        return f"Published {location}. Outcome: {state.outcome}. Findings remain unchanged."

    return execute


class PhaseTools:
    """Expose investigation or publishing capabilities according to checkpointed phase."""

    def __init__(
        self, root: Path, common: list[Tool], investigation: list[Tool], publisher: Publisher
    ) -> None:
        self.root = root
        self.common = common
        self.investigation = investigation
        self.publisher = publisher
        self.publishing: list[Tool] | None = None

    async def tools(self) -> list[Tool]:
        if store_as(FindingsState).phase == "investigating":
            return [*self.common, *self.investigation]
        if self.publishing is None:
            # Neither these skill descriptions nor their files enter the model's
            # workspace before submission. Resume resolves the same declared package.
            self.publishing = [
                skill(self.publisher.skills),
                *self.publisher.tools(self.root),
                publish_document(self.root, self.publisher),
            ]
        return [*self.common, *self.publishing]


@task
def publish(
    bundle: str,
    publisher: str = "gl",
    output_dir: str = "./publications",
    instructions: str = "",
    box: str = "docker",
    investigator_image: str | None = None,
    token_limit: int | None = None,
) -> Task:
    """Publish an existing findings bundle, without loading the benchmark or launching jobs.

    Args:
        bundle: Local directory containing findings.json, manifest.json and evidence.
        publisher: 'gl' or an installed module:factory returning a Publisher.
        output_dir: Where immutable report bundles are saved.
        instructions: Presentation instructions for the publishing agent.
        box: docker or k8s, independently of where the original investigation ran.
        investigator_image: Optional published image for the Kubernetes workspace.
        token_limit: Inspect token limit for this publishing run.
    """
    from ._investigate import DEFAULT_INVESTIGATOR_IMAGE, docker_box
    from ._investigation_workspace import configure, stage_inputs

    source = Path(bundle).expanduser().resolve()
    if source.name == "findings.json" and source.is_file():
        source = source.parent
    findings, manifest = read_bundle(source)
    root = Path(output_dir).expanduser().resolve() / uuid4().hex
    (root / "inputs").mkdir(parents=True)
    (root / "work/report").mkdir(parents=True)
    shutil.copytree(source, root / "inputs/findings")
    selected = load_publisher(publisher)
    tools = PhaseTools(
        root,
        [
            ToolDef(bash(timeout=300), max_output=128 * 1024).as_tool(),
            view_image(),
            report_blocker(),
        ],
        [],
        selected,
    )

    @solver
    def begin() -> Solver:
        async def solve(state: TaskState, generate: Generate) -> TaskState:
            saved = state.store_as(FindingsState)
            saved.findings = findings.model_dump(mode="json")
            saved.manifest = manifest
            saved.phase = "publishing"
            return state

        return solve

    async def continuing(state: AgentState) -> bool | str:
        saved = store_as(InvestigationState)
        if saved.published or saved.outcome == "blocked":
            return False
        return (
            True
            if state.output.message.tool_calls
            else "Continue publishing from the submitted findings, or report the concrete blocker."
        )

    if box not in ("docker", "k8s"):
        raise ValueError("box must be docker or k8s")
    environment = (
        docker_box(root)
        if box == "docker"
        else configure(root, investigator_image or DEFAULT_INVESTIGATOR_IMAGE)
    )
    return Task(
        dataset=[
            Sample(
                input=f"Findings are final at /inputs/findings/findings.json. Read their manifest and invoke the publishing skill. {instructions}"
            )
        ],
        setup=[begin(), stage_inputs(root)],
        solver=react(
            name="publisher",
            prompt="Publish the submitted findings faithfully. Use only existing evidence and calculations. No new experiments or changes to findings. Read the publishing skill.",
            tools=[tools],
            submit=False,
            on_continue=continuing,
        ),
        sandbox=environment,
        scorer=investigation_outcome(),
        checkpoint=CheckpointConfig(sandbox_paths={"default": ["/workspace", "/inputs"]}),
        token_limit=token_limit,
        metadata={"investigation_dir": str(root), "publisher": publisher},
    )
