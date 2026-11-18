"""The Epoch AI benchmark review page, generated from frozen findings.

The auditor rates the rubric's rows in `auditor.ratings` during the audit (the
epoch-review skill says how) and, after submission, writes the page's prose blocks to
`/workspace/report/prose.json`. Everything else on the page, the verdict, the defect
rate, the rubric tables and the list of audited items, is derived from the bundle by
`inspect_audit._epoch`, so the page cannot say more than the findings show.
"""

import json
import shutil
from pathlib import Path
from typing import Any

from inspect_ai.tool import Tool, ToolError, tool
from inspect_ai.util import sandbox

from .. import _epoch
from .._findings import Findings, encoded, read_bundle
from .._publishing import Publisher
from ._render import reported

PROSE = "/workspace/report/prose.json"
PAGE = "epoch_review.md"


def validate(findings: Findings) -> None:
    """What submission requires.

    Rubric rows on their scales, the rubric's stop rule, the log-dependent rows
    unrated when no logs were supplied, and a scoring row that agrees with its measure.
    """
    _epoch.validate(findings)


def report(findings: Findings, prose: _epoch.Prose) -> dict[str, Any]:
    return _epoch.review(findings, prose)


async def _prose() -> _epoch.Prose:
    try:
        text = await sandbox().read_file(PROSE)
    except FileNotFoundError as ex:
        raise ToolError(f"Write {PROSE} using the epoch-publishing skill") from ex
    try:
        return _epoch.Prose.model_validate_json(text)
    except ValueError as ex:
        raise ToolError(f"{PROSE} is not a valid prose record: {ex}") from ex


async def build(root: Path, bundle: Path) -> Path:
    findings, manifest = read_bundle(bundle)
    prose = await _prose()
    derived = report(findings, prose)
    page = derived.pop("page")
    output = root / "rendered-epoch"
    if output.exists():
        shutil.rmtree(output)
    output.mkdir()
    (output / PAGE).write_text(page)
    (output / "verdict.json").write_bytes(
        encoded(
            {
                key: derived[key]
                for key in (
                    "verdict",
                    "reasons",
                    "measures",
                    "methodology",
                    "template",
                    "logs_supplied",
                    "sampling",
                )
            }
            | {"findings_sha256": manifest["files"]["findings.json"]["sha256"]}
        )
    )
    (output / "coverage.json").write_bytes(encoded(derived["coverage"]))
    (output / "assessments.json").write_bytes(encoded(derived["assessments"]))
    (output / "prose.json").write_bytes(encoded(prose.model_dump(mode="json")))
    # the description the page was rendered against, so a reader can check a row's
    # form and scale against what the auditor was asked
    (output / "epoch.json").write_bytes(_epoch.DESCRIPTION.read_bytes())
    return output


@tool(parallel=False)
def preview_review(root: Path) -> Tool:
    async def execute() -> str:
        """Render the Epoch review page from the frozen findings and prose.json.

        Writes /workspace/report/epoch_review.md and returns the derived verdict with
        its reasons. Revise prose.json and call again; publish_document when it reads
        well.
        """
        from .._findings import restore_bundle

        with reported("Preview"):
            output = await build(root, await restore_bundle(root))
        await sandbox().write_file(f"/workspace/report/{PAGE}", (output / PAGE).read_text())
        verdict = json.loads((output / "verdict.json").read_text())
        return (
            f"Preview ready at /workspace/report/{PAGE}. Verdict {verdict['verdict']}: "
            + "; ".join(verdict["reasons"])
            + ". Read the page as its reader would, revise prose.json, then publish_document."
        )

    return execute


def publisher() -> Publisher:
    here = Path(__file__).parent
    return Publisher(
        skills=[str(here / "skills/epoch-publishing")],
        build=build,
        tools=lambda root: [preview_review(root)],
        validate=validate,
        audit_skills=[str(here.parent / "audit/skills/epoch-review")],
        stage=_epoch.stage,
    )
