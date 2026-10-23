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

from ._findings import Findings, FindingsState, encoded, read_bundle, restore_bundle
from ._image import view_image
from ._report import AuditState, audit_outcome, report_blocker


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
    validate: Callable[[Findings], None] = lambda findings: None


def load_publisher(name: str) -> Publisher:
    if name == "latex":
        from .publishers.latex import publisher

        return publisher()
    if name == "gl":
        from .publishers.gl import publisher

        return publisher()
    module, separator, factory = name.partition(":")
    if not separator:
        raise ValueError("publisher must be 'latex', 'gl' or an installed module:factory")
    result = getattr(importlib.import_module(module), factory)()
    if not isinstance(result, Publisher):
        raise TypeError("Publisher factories must return inspect_audit.Publisher")
    return result


@tool(parallel=False)
def publish_document(root: Path, publisher: Publisher) -> Tool:
    async def execute() -> str:
        """Build the report from final findings and preserve its output bundle."""
        from ._audit_workspace import persist

        bundle = await restore_bundle(root)
        # Submission preserves missing work explicitly. A report does not turn it into coverage.
        from ._findings import Findings
        from ._register import Register, apply_reviews, select
        from ._verdicts import InvestigationItemSkill

        findings = Findings.model_validate(store_as(FindingsState).findings)
        checks = TypeAdapter(dict[str, InvestigationItemSkill]).validate_python(
            findings.investigators["checks"]
        )
        register = Register.model_validate(
            {"checks": list(checks), "samples": findings.investigators["samples"]}
        )
        apply_reviews(register, findings.auditor.reviews, checks)
        unresolved = select(register, "missing", checks, None) or select(
            register, "unresolved", checks, None
        )
        outcome = "published_incomplete" if unresolved else "published_complete"
        reasons = (
            ["Some planned sample/check cells are missing or unresolved"]
            if outcome == "published_incomplete"
            else []
        )
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
        state = store_as(AuditState)
        state.outcome = outcome
        state.outcome_reasons = reasons
        state.published = location
        return f"Published {location}. Outcome: {state.outcome}. Findings remain unchanged."

    return execute


class PhaseTools:
    """Expose audit or publishing capabilities according to checkpointed phase."""

    def __init__(
        self, root: Path, common: list[Tool], audit: list[Tool], publisher: Publisher
    ) -> None:
        self.root = root
        self.common = common
        self.audit = audit
        self.publisher = publisher
        self.publishing: list[Tool] | None = None

    async def tools(self) -> list[Tool]:
        if store_as(FindingsState).phase == "auditing":
            return [*self.common, *self.audit]
        if self.publishing is None:
            # Neither these skill descriptions nor their files enter the model's
            # workspace before submission. Resume resolves the same declared package.
            self.publishing = [
                # its own instance: the audit skill tool's installed list is
                # stored per instance and would otherwise answer for this one
                skill(self.publisher.skills, instance="publishing"),
                *self.publisher.tools(self.root),
                publish_document(self.root, self.publisher),
            ]
        return [*self.common, *self.publishing]


@task
def publish(
    bundle: str,
    publisher: str = "latex",
    output_dir: str = "./publications",
    instructions: str = "",
    box: str = "docker",
    auditor_image: str | None = None,
    token_limit: int | None = None,
) -> Task:
    """Publish an existing findings bundle, without loading the benchmark or launching jobs.

    Args:
        bundle: Local directory containing findings.json, manifest.json and evidence.
        publisher: 'latex', 'gl' or an installed module:factory returning a Publisher.
        output_dir: Where immutable report bundles are saved.
        instructions: Presentation instructions for the publishing agent.
        box: docker or k8s, independently of where the original audit ran.
        auditor_image: Optional published image for the Kubernetes workspace.
        token_limit: Inspect token limit for this publishing run.
    """
    from ._audit import DEFAULT_AUDITOR_IMAGE, docker_box
    from ._audit_workspace import configure, stage_inputs

    source = Path(bundle).expanduser().resolve()
    if source.name == "findings.json" and source.is_file():
        source = source.parent
    findings, manifest = read_bundle(source)
    root = Path(output_dir).expanduser().resolve() / uuid4().hex
    (root / "inputs").mkdir(parents=True)
    (root / "work/report").mkdir(parents=True)
    shutil.copytree(source, root / "inputs/findings")
    selected = load_publisher(publisher)
    selected.validate(findings)
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
        saved = store_as(AuditState)
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
        else configure(root, auditor_image or DEFAULT_AUDITOR_IMAGE)
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
        scorer=audit_outcome(),
        checkpoint=CheckpointConfig(sandbox_paths={"default": ["/workspace", "/inputs"]}),
        token_limit=token_limit,
        metadata={"audit_dir": str(root), "publisher": publisher},
    )
