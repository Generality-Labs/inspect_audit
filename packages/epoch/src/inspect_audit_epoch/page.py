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

from inspect_audit.package import (
    Findings,
    Publisher,
    encoded,
    read_bundle,
    reported,
    restore_bundle,
)

from . import method as _epoch

PROSE = "/workspace/report/prose.json"
PAGE = "epoch_review.md"


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
    return render(
        root / "rendered-epoch", findings, prose, manifest["files"]["findings.json"]["sha256"]
    )


def render(output: Path, findings: Findings, prose: _epoch.Prose, findings_sha256: str) -> Path:
    """Write the page and its derived records to `output`, replacing what was there."""
    derived = report(findings, prose)
    page = derived.pop("page")
    if output.exists():
        shutil.rmtree(output)
    output.mkdir(parents=True)
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
            | {"findings_sha256": findings_sha256}
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


def _findings_from(source: Path) -> tuple[Findings, str, Path | None]:
    """Findings from what an audit saved, with their digest.

    A published folder, a findings bundle, a findings.json or the audit's eval log; a
    published folder also yields the prose.json it was rendered with.
    """
    import hashlib

    if source.is_dir() and (source / "findings/manifest.json").exists():
        findings, manifest = read_bundle(source / "findings")
        prose = source / "rendered-epoch/prose.json"
        return (
            findings,
            manifest["files"]["findings.json"]["sha256"],
            prose if prose.exists() else None,
        )
    if source.is_dir() and (source / "manifest.json").exists():
        findings, manifest = read_bundle(source)
        return findings, manifest["files"]["findings.json"]["sha256"], None
    if source.suffix == ".json":
        raw = source.read_bytes()
        return Findings.model_validate_json(raw), hashlib.sha256(raw).hexdigest(), None
    if source.suffix == ".eval":
        from inspect_ai.log import read_eval_log

        log = read_eval_log(str(source))
        for sample in log.samples or []:
            if sample.store and "FindingsState:findings" in sample.store:
                raw = encoded(sample.store["FindingsState:findings"])
                return Findings.model_validate_json(raw), hashlib.sha256(raw).hexdigest(), None
        raise SystemExit(f"{source}: no audit sample with submitted findings")
    raise SystemExit(
        f"{source}: not a published folder, a findings bundle, a findings.json or an eval log"
    )


def main(argv: list[str] | None = None) -> int:
    """Re-render the Epoch review page from saved findings, without a model or a box.

    The audit publishes once; this renders the same page again from what it saved,
    after a fix to the renderer, from a `hawk download-artifacts` folder, or from the
    audit's own eval log when nothing else survived the pod. The prose is the auditor's
    prose.json: found beside the findings in a published folder, else given.
    """
    import argparse

    parser = argparse.ArgumentParser(description=(main.__doc__ or "").split("\n\n")[0])
    parser.add_argument(
        "source",
        type=Path,
        help="a published/<id> folder, a findings bundle, a findings.json, or the audit's .eval log",
    )
    parser.add_argument("--prose", type=Path, help="prose.json, when the source does not carry one")
    parser.add_argument("--out", type=Path, default=Path("epoch-review"), help="output folder")
    args = parser.parse_args(argv)
    findings, digest, found = _findings_from(args.source)
    prose_path = args.prose or found
    if prose_path is None:
        parser.error("no prose.json beside the findings; pass --prose")
    prose = _epoch.Prose.model_validate_json(prose_path.read_text())
    output = render(args.out, findings, prose, digest)
    verdict = json.loads((output / "verdict.json").read_text())
    print(f"{output / PAGE}: {verdict['verdict']}; " + "; ".join(verdict["reasons"]))
    return 0


def publisher() -> Publisher:
    here = Path(__file__).parent
    return Publisher(
        skills=[str(here / "skills/epoch-publishing")],
        build=build,
        tools=lambda root: [preview_review(root)],
    )


if __name__ == "__main__":
    raise SystemExit(main())
