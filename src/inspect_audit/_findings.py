"""Editable findings drafts and a portable, immutable submission boundary."""

import asyncio
import hashlib
import json
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal
from uuid import uuid4

from inspect_ai.tool import Tool, ToolError, tool
from inspect_ai.util import StoreModel, sandbox, store_as
from pydantic import BaseModel, ConfigDict, Field, JsonValue

from ._verdicts import Evidence

if TYPE_CHECKING:
    from ._investigate import Investigation
    from ._register import Register

DRAFT = "/workspace/findings.json"


class Finding(BaseModel):
    model_config = ConfigDict(extra="forbid")
    criterion: str = ""
    issue: str = Field(min_length=1)
    explanation: str = Field(min_length=1)
    sample_ids: list[str] = Field(default_factory=list)
    evidence: list[Evidence] = Field(min_length=1)


class Review(BaseModel):
    model_config = ConfigDict(extra="forbid")
    sample_id: str
    check: str
    label: str
    explanation: str = Field(min_length=1)
    evidence: list[Evidence] = Field(min_length=1)


class Rating(BaseModel):
    model_config = ConfigDict(extra="forbid")
    label: str = Field(min_length=1)
    explanation: str = Field(min_length=1)
    evidence: list[Evidence] = Field(min_length=1)


class InvestigatorFindings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    summary: str = ""
    findings: list[Finding] = Field(default_factory=list)
    reviews: list[Review] = Field(default_factory=list)
    ratings: dict[str, Rating] = Field(default_factory=dict)
    limitations: list[str] = Field(default_factory=list)
    artifacts: list[str] = Field(default_factory=list)


class Findings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    subject: dict[str, JsonValue] = Field(default_factory=dict)
    auditors: dict[str, JsonValue] = Field(default_factory=lambda: {"checks": {}, "samples": {}})
    investigator: InvestigatorFindings = Field(default_factory=InvestigatorFindings)


class FindingsState(StoreModel):
    """Authoritative submission and manifest, carried by Inspect checkpoints."""

    phase: Literal["investigating", "publishing"] = "investigating"
    findings: dict[str, Any] = Field(default_factory=dict)
    manifest: dict[str, Any] = Field(default_factory=dict)
    destination: str | None = None
    required_coverage: float | None = None


def require_investigation() -> None:
    if store_as(FindingsState).phase != "investigating":
        raise ToolError("Findings are final. Investigation and experiment tools are disabled.")


def encoded(value: Any) -> bytes:
    return (json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + "\n").encode()


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def auditor_results(register: "Register") -> dict[str, Any]:
    from ._verdicts import audit_items

    return {
        "checks": {s.name: s.model_dump(mode="json") for s in audit_items(register.checks)},
        "samples": {
            sample: {
                check: [
                    take.model_dump(mode="json", exclude_none=True)
                    for take in takes
                    if take.by == "auditor"
                ]
                for check, takes in checks.items()
            }
            for sample, checks in register.samples.items()
        },
    }


async def sync_draft(register: "Register") -> None:
    """Refresh collected auditor records without replacing the investigator's draft."""
    require_investigation()
    try:
        data = json.loads(await sandbox().read_file(DRAFT))
    except FileNotFoundError:
        data = Findings().model_dump(mode="json")
    except ValueError as ex:
        raise ToolError(
            "findings.json is not valid JSON; repair the draft and collect again"
        ) from ex
    if not isinstance(data, dict):
        raise ToolError("findings.json must be an object")
    data["subject"] = register.subject
    data["auditors"] = auditor_results(register)
    await sandbox().write_file(DRAFT, encoded(data))


def validate_draft(data: Any, register: "Register") -> Findings:
    draft = Findings.model_validate(data)
    if not draft.investigator.summary.strip():
        raise ValueError("The investigator summary must explain what the investigation established")
    if draft.auditors != auditor_results(register) or draft.subject != register.subject:
        raise ValueError(
            "Auditor records and subject must match the collected results; collect again to refresh them"
        )
    samples = set(register.samples)
    for finding in draft.investigator.findings:
        if set(finding.sample_ids) - samples:
            raise ValueError("A finding references samples outside the declared population")
        if not finding.issue.strip() or not finding.explanation.strip():
            raise ValueError("Findings need an issue and explanation")
    from ._verdicts import audit_items

    checks = {s.name: s for s in audit_items(register.checks)}
    from ._register import apply_reviews

    apply_reviews(register.model_copy(deep=True), draft.investigator.reviews, checks)
    for review in draft.investigator.reviews:
        if not review.explanation.strip():
            raise ValueError("Reviews need an explanation")
    for rating in draft.investigator.ratings.values():
        if not rating.label.strip() or not rating.explanation.strip():
            raise ValueError("Ratings need a label and explanation")
    observations = [e for finding in draft.investigator.findings for e in finding.evidence]
    observations += [e for review in draft.investigator.reviews for e in review.evidence]
    observations += [e for rating in draft.investigator.ratings.values() for e in rating.evidence]
    for evidence in observations:
        if not evidence.observed.strip() or not evidence.source.strip():
            raise ValueError("Evidence needs an observation and its source")
    return draft


async def _artifact(root: Path, path: str) -> tuple[str, bytes]:
    """Read an explicitly selected artifact, without traversing outside staged roots."""
    original = Path(path)
    if ".." in original.parts or not original.is_absolute():
        raise ValueError(f"Artifact paths must be absolute under /inputs or /workspace: {path}")
    if original.is_relative_to("/inputs"):
        relative = original.relative_to("/inputs")
        host = root / "inputs" / relative
        name = "inputs/" + relative.as_posix()
    elif original.is_relative_to("/workspace"):
        relative = original.relative_to("/workspace")
        host = root / "work" / relative
        name = "workspace/" + relative.as_posix()
    else:
        raise ValueError(f"Artifact is outside /inputs and /workspace: {path}")
    allowed = root / ("inputs" if name.startswith("inputs/") else "work")
    # Prefer the trusted inputs (including collected logs), not a sandbox replacement.
    if name.startswith("inputs/") and host.exists():
        if host.is_symlink() or not host.resolve().is_relative_to(allowed.resolve()):
            raise ValueError(f"Artifact escapes its input root: {path}")
        content = host.read_bytes()
    else:
        probe = await sandbox().exec(
            [
                "sh",
                "-c",
                'p=$(realpath -- "$1") && test -f "$1" && test ! -L "$1" && test "$p" = "$1"',
                "sh",
                path,
            ]
        )
        if not probe.success:
            raise ValueError(
                f"Artifact must be an existing regular file without symlink traversal: {path}"
            )
        content = await sandbox().read_file(path, text=False)
    return name, content


async def freeze(root: Path, draft: Findings) -> Path:
    """Save primary observations, declared artifacts and a content manifest."""
    from ._report import InvestigationRecord, job_addresses, log_address

    files: dict[str, bytes] = {"findings.json": encoded(draft.model_dump(mode="json"))}
    refs: list[dict[str, Any]] = []
    # Native logs stay at their original address. Hash trusted collected files in
    # chunks instead of loading large transcripts into the store or report bundle.
    native_paths = [
        p
        for folder in (root / "inputs/logs", root / "inputs/jobs")
        if folder.exists()
        for p in folder.rglob("*")
        if p.is_file() and p.suffix in (".eval", ".json")
    ]
    for path in native_paths:
        if path.is_symlink() or not path.resolve().is_relative_to((root / "inputs").resolve()):
            raise ValueError(f"Log reference escapes the staged inputs: {path}")
        with path.open("rb") as handle:
            digest = hashlib.file_digest(handle, "sha256").hexdigest()
        relative = path.relative_to(root / "inputs")
        refs.append(
            {
                "source": "/inputs/" + relative.as_posix(),
                "address": log_address(root, relative, job_addresses()),
                "sha256": digest,
                "bytes": path.stat().st_size,
            }
        )
    for path in dict.fromkeys(draft.investigator.artifacts):
        if any(ref["source"] == path for ref in refs):
            continue
        name, content = await _artifact(root, path)
        if name.endswith(".eval") or (
            name.startswith("inputs/")
            and (name.startswith(("inputs/logs/", "inputs/jobs/")) and name.endswith(".json"))
        ):
            if not path.startswith("/inputs/"):
                raise ValueError("Native logs must come from declared inputs or collected jobs")
            refs.append(
                {
                    "source": path,
                    "address": log_address(
                        root, Path(path).relative_to("/inputs"), job_addresses()
                    ),
                    "sha256": sha256(content),
                    "bytes": len(content),
                }
            )
        else:
            files["evidence/" + name] = content
    if sum(map(len, files.values())) > 90 * 2**20:
        raise ValueError(
            "Findings bundle exceeds 90 MiB; select derived tables and evidence extracts"
        )
    manifest = {
        "files": {
            name: {"sha256": sha256(content), "bytes": len(content)}
            for name, content in files.items()
        },
        "artifacts": {
            path: "evidence/"
            + (
                "inputs/" + path.removeprefix("/inputs/")
                if path.startswith("/inputs/")
                else "workspace/" + path.removeprefix("/workspace/")
            )
            for path in draft.investigator.artifacts
            if not any(ref["source"] == path for ref in refs)
        },
        "log_references": refs,
        "jobs": store_as(InvestigationRecord).model_dump(mode="json")["jobs"],
        "required_coverage": store_as(FindingsState).required_coverage,
    }
    destination = root / "submitted" / uuid4().hex
    destination.mkdir(parents=True)
    for name, content in {**files, "manifest.json": encoded(manifest)}.items():
        target = destination / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
    await stage_bundle(destination)
    if (root / "artifacts.json").exists():
        from ._investigation_workspace import persist

        await persist(root, destination, "findings")
    state = store_as(FindingsState)
    state.findings = draft.model_dump(mode="json")
    state.manifest = manifest
    state.destination = str(destination)
    state.phase = "publishing"
    return destination


async def stage_bundle(bundle: Path) -> None:
    import io
    import tarfile

    from ._investigation_workspace import write_into

    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w:gz") as archive:
        archive.add(bundle, arcname="findings", recursive=True)
    await write_into(stream.getvalue(), "/inputs")


def read_bundle(bundle: Path) -> tuple[Findings, dict[str, Any]]:
    """Validate a saved handoff without consulting an investigation's conversation."""
    manifest = json.loads((bundle / "manifest.json").read_text())
    if not isinstance(manifest, dict) or not isinstance(manifest.get("files"), dict):
        raise ValueError("Manifest must contain a files mapping")
    if any(target not in manifest["files"] for target in manifest.get("artifacts", {}).values()):
        raise ValueError("Artifact mapping must reference files in the bundle manifest")
    required = manifest.get("required_coverage")
    if required is not None and (
        isinstance(required, bool)
        or not isinstance(required, (int, float))
        or not 0 < required <= 1
    ):
        raise ValueError("required_coverage must be a fraction in (0, 1]")
    if "findings.json" not in manifest["files"]:
        raise ValueError("Manifest must include findings.json")
    entries = list(bundle.rglob("*"))
    if any(path.is_symlink() for path in entries):
        raise ValueError("Findings bundles cannot contain symlinks")
    actual = {path.relative_to(bundle).as_posix() for path in entries if path.is_file()}
    if actual != set(manifest["files"]) | {"manifest.json"}:
        raise ValueError("Bundle files must match the manifest exactly")
    if any(name.endswith(".eval") for name in manifest["files"]):
        raise ValueError("Native logs belong in external log references")
    for name, record in manifest["files"].items():
        path = bundle / name
        if (
            Path(name).is_absolute()
            or ".." in Path(name).parts
            or path.is_symlink()
            or not path.resolve().is_relative_to(bundle.resolve())
        ):
            raise ValueError(f"Invalid bundle path: {name}")
        content = path.read_bytes()
        if sha256(content) != record["sha256"] or len(content) != record["bytes"]:
            raise ValueError(f"Bundle checksum mismatch: {name}")
    return Findings.model_validate_json((bundle / "findings.json").read_text()), manifest


async def restore_bundle(root: Path) -> Path:
    """Restore trusted host files after resume, checking the checkpointed manifest."""
    state = store_as(FindingsState)
    if state.phase != "publishing":
        raise ToolError("Submit findings before publishing")
    if state.destination and Path(state.destination).is_dir():
        saved, manifest = read_bundle(Path(state.destination))
        if saved.model_dump(mode="json") != state.findings or manifest != state.manifest:
            raise ToolError("Trusted submitted bundle does not match its checkpoint")
        return Path(state.destination)
    destination = root / "submitted" / "active"
    destination.mkdir(parents=True, exist_ok=True)
    for name, record in state.manifest["files"].items():
        target = destination / name
        content = (
            encoded(state.findings)
            if name == "findings.json"
            else await sandbox().read_file(f"/inputs/findings/{name}", text=False)
        )
        if sha256(content) != record["sha256"]:
            raise ToolError(f"Submitted evidence changed: {name}")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
    (destination / "manifest.json").write_bytes(encoded(state.manifest))
    return destination


@tool(parallel=False)
def submit_findings(
    root: Path,
    investigation: "Investigation | None" = None,
    task: str | None = None,
    validate: Callable[[Findings], None] | None = None,
) -> Tool:
    async def execute() -> str:
        """Validate and freeze /workspace/findings.json, then enter publishing.

        Finish and collect all child jobs first. Include the files needed for a
        fresh publisher in investigator.artifacts. Submission cannot be reopened.
        """
        from ._register import load
        from .dispatch import FINISHED

        lock = investigation.launch_lock if investigation else asyncio.Lock()
        async with lock:
            require_investigation()
            if (task or (investigation and investigation.task)) and not load().checks:
                raise ToolError("Start the sample population with start_register before submission")
            if investigation:
                for job in investigation.record().jobs:
                    if await investigation.runs.status(job["run_id"]) not in FINISHED:
                        raise ToolError(
                            f"Finish or stop job {job['label']} before submitting findings"
                        )
                    if not job.get("collected") and not job.get("collection_error"):
                        raise ToolError(f"Collect job {job['label']} before submitting findings")
            try:
                draft = validate_draft(json.loads(await sandbox().read_file(DRAFT)), load())
                if validate is not None:
                    validate(draft)
                await freeze(root, draft)
            except (ValueError, OSError) as ex:
                raise ToolError(f"Findings submission failed: {ex}") from ex
            return "Findings are final at /inputs/findings/findings.json. Experiment tools are disabled. Publishing tools are now available; invoke the publishing skill."

    return execute
