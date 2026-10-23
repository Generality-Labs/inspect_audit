"""Laurence's pinned GL template, with structure generated from frozen findings."""

import asyncio
import json
import os
import re
import shutil
from pathlib import Path
from typing import Any

from inspect_ai.tool import Tool, ToolError, tool
from inspect_ai.util import sandbox, subprocess
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from .._findings import Findings, encoded, read_bundle
from .._framework import REGISTRY
from .._publishing import Publisher
from .._verdicts import Evidence
from ._template import template

ASSESSMENTS = {
    label: r"\assess" + macro
    for label, macro in (
        ("None", "None"),
        ("Minor", "Minor"),
        ("Major", "Major"),
        ("Critical", "Critical"),
        ("Not assessed", "NotAssessed"),
        ("Not applicable", "NA"),
    )
}


class Prose(BaseModel):
    """Presentation text only; no grades, rows, counts, references or TeX."""

    model_config = ConfigDict(extra="forbid")
    datasheet: str = ""
    setup: str = ""
    dimensions: dict[str, str] = Field(default_factory=dict)


def tex(value: object) -> str:
    substitutions = {
        "\\": r"\textbackslash{}",
        "&": r"\&",
        "%": r"\%",
        "$": r"\$",
        "#": r"\#",
        "_": r"\_",
        "{": r"\{",
        "}": r"\}",
        "~": r"\textasciitilde{}",
        "^": r"\textasciicircum{}",
    }
    return "".join(substitutions.get(c, c) for c in str(value)).replace("\n\n", "\\par ")


def validate(findings: Findings) -> None:
    definitions = json.loads(REGISTRY.read_text())["dimensions"]
    ratings = findings.auditor.ratings
    if (
        "overall" not in ratings
        or ratings["overall"].label not in "ABCD"
        or len(ratings["overall"].label) != 1
    ):
        raise ValueError(
            "LaTeX publication needs ratings.overall with an A-D label, explanation and evidence before submission"
        )
    for key, rating in ratings.items():
        if key != "overall" and (key not in definitions or rating.label not in ASSESSMENTS):
            raise ValueError(f"Unknown framework criterion or assessment: {key}={rating.label}")
    for finding in findings.auditor.findings:
        if finding.criterion not in definitions:
            raise ValueError(
                "Each LaTeX finding needs a framework criterion, e.g. C or C.1, before submission"
            )


def source(
    findings: Findings, manifest: dict[str, Any], prose: Prose, upstream: Path
) -> tuple[str, str]:
    """Generate all template structure; prose values are escaped literal text."""
    validate(findings)
    definitions = json.loads(REGISTRY.read_text())["dimensions"]
    dimensions = {k: v for k, v in definitions.items() if "." not in k}
    if set(prose.dimensions) - set(dimensions):
        raise ValueError("Prose dimension keys must be A-I")
    ratings = findings.auditor.ratings
    rows = TypeAdapter(dict[str, dict[str, list[dict[str, Any]]]]).validate_python(
        findings.investigators["samples"]
    )
    samples = len(rows)
    investigated = sum(any(takes for takes in checks.values()) for checks in rows.values())

    def assessment(key: str) -> str:
        return ASSESSMENTS[ratings[key].label] if key in ratings else ASSESSMENTS["Not assessed"]

    def explanation(key: str) -> str:
        return (
            tex(ratings[key].explanation)
            if key in ratings
            else "Not assessed in the submitted findings."
        )

    def evidence(records: list[Evidence]) -> str:
        return r"\par ".join(tex(e.observed) + " --- " + tex(e.source) for e in records)

    def field(name: str, value: object) -> str:
        return r"\dsfield{" + name + "}{" + tex(value) + "}\n"

    def box(title: str, content: str) -> str:
        return r"\begin{dsbox}[breakable]{" + title + "}\n" + content + "\n" + r"\end{dsbox}" + "\n"

    parts = []
    for letter, definition in dimensions.items():
        contributions = " ".join(
            r"\issue{"
            + key
            + "}{"
            + tex(definitions[key]["name"])
            + "}{"
            + tex(rating.explanation)
            + "}"
            for key, rating in ratings.items()
            if key.startswith(letter + ".") and rating.label in ("Minor", "Major", "Critical")
        ) or explanation(letter)
        parts.append(
            r"\setdimension{"
            + definition["dimension"]
            + "}{"
            + assessment(letter)
            + "}{"
            + contributions
            + "}"
        )
    parts += [
        r"\gdef\ppartname{Cover Sheet}\auditdestination{cover}",
        r"\begin{adjustwidth}{5mm}{5mm}\reporttitle{Cover Sheet}",
        box(
            "Document",
            field("Title", findings.subject.get("task", "Evaluation audit"))
            + field("Report no.", manifest["files"]["findings.json"]["sha256"][:12]),
        ),
        box("Executive Summary", tex(findings.auditor.summary)),
        box(
            "Traceability",
            field("Prepared by", "Automated audit")
            + field("Verified by", "Not recorded")
            + field("Approved by", "Not recorded"),
        ),
        box("Distribution", "Generated from immutable submitted findings."),
        box("Revision History", "This publication preserves the submitted findings checksum."),
        r"\end{adjustwidth}\begin{maincontentstyled}",
    ]
    # Reuse the pinned Example's navigation and scorecard layout.
    example = (upstream / "reports/Example/Content.tex").read_text()
    parts.append(
        example[
            example.index(r"\auditpart{Contents}") : example.index(r"\auditpart{Eval Scorecard}")
        ]
    )
    scorecard = example[
        example.index(r"\auditpart{Eval Scorecard}") : example.index(r"\auditpart{Eval Datasheet}")
    ]
    replacements = {
        r"[e.g.\ Agentic data analysis / Single-turn prompt response]": "type",
        "[Capability/Safety -- domain]": "domain",
        "[what the headline metric claims to measure]": "measurement_aim",
        "[release date, leaderboard, maintenance status]": "status",
    }
    values = {
        placeholder: tex(findings.subject.get(key, "Not recorded"))
        for placeholder, key in replacements.items()
    }
    values.update(
        dict.fromkeys(
            (
                "[sandbox/API, dataset size, content handling]",
                r"[cost per model/episode incl.\ judges; audit total]",
                "[wall-clock per model/episode]",
                r"[port location; commit \ccommitref]",
            ),
            "Not recorded",
        )
    )
    # One pass over template placeholders: inserted findings are never reinterpreted.
    scorecard = re.sub(
        r"\[Headline assessment:.*?\]|" + "|".join(re.escape(key) for key in values),
        lambda match: (
            tex(ratings["overall"].explanation) + r"\par " + evidence(ratings["overall"].evidence)
            if match[0].startswith("[Headline assessment:")
            else values[match[0]]
        ),
        scorecard,
        flags=re.DOTALL,
    )
    parts.append(scorecard)
    subject = "".join(
        field(
            tex(key),
            json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list)) else value,
        )
        for key, value in findings.subject.items()
    )
    parts += [
        r"\auditpart{Eval Datasheet}{datasheet}\begin{reportpart}{Eval Datasheet}{datasheet}{sec:datasheet}\auditdestination{datasheet}",
        box("Benchmark", subject),
        box("Overview", tex(prose.datasheet)),
        r"\end{reportpart}",
        r"\auditpart{Audit Setup}{methodology}\begin{reportpart}{Audit Setup}{methodology}{sec:methodology}\auditdestination{methodology}",
        box("Scope of Report", tex(prose.setup)),
        box(
            "Run Setup",
            field("Samples", f"{investigated}/{samples} selected samples have investigator results")
            + field("Jobs", len(manifest.get("jobs", []))),
        ),
        box(
            "Models",
            tex(
                ", ".join(
                    sorted(
                        {
                            take["model"]
                            for checks in rows.values()
                            for takes in checks.values()
                            for take in takes
                            if take.get("model")
                        }
                    )
                )
                or "Not recorded"
            ),
        ),
        box("Limitations", tex("\n\n".join(findings.auditor.limitations) or "None recorded.")),
        box(
            "Log references",
            r"\par ".join(
                tex(ref["address"]) + " --- " + tex(ref["sha256"])
                for ref in manifest.get("log_references", [])
            )
            or "None recorded.",
        ),
        r"\end{reportpart}",
        r"\auditpart{Audit Findings}{audit}\begin{reportpart}{Audit Findings}{audit}{sec:auditreport}\auditdestination{findings}",
    ]
    for letter, definition in dimensions.items():
        key = definition["dimension"]
        contributions = "\n".join(
            r"\contributionrow{"
            + code
            + "}{"
            + tex(row["name"])
            + "}{"
            + assessment(code)
            + "}{"
            + explanation(code)
            + (r"\par " + evidence(ratings[code].evidence) if code in ratings else "")
            + "}"
            for code, row in definitions.items()
            if code.startswith(letter + ".")
        )
        parts += [
            r"\begin{dimensionreview}{" + key + "}",
            r"\dimensionoverview{" + key + "}{" + explanation(letter) + "}{" + contributions + "}",
            tex(prose.dimensions.get(letter, "")),
        ]
        for finding in findings.auditor.findings:
            if finding.criterion.split(".")[0] != letter:
                continue
            # Contribution findings use A.n; holistic findings retain sequential F-n.
            label = "[" + finding.criterion + "]" if "." in finding.criterion else ""
            rating = ratings.get(finding.criterion) or ratings.get(letter)
            severity = r"\sev" + (
                rating.label if rating and rating.label in ("Minor", "Major", "Critical") else "Obs"
            )
            parts += [
                r"\begin{finding}" + label + "{" + tex(finding.issue) + "}{" + severity + "}",
                r"\findingfield{Criterion}{" + tex(finding.criterion) + "}",
                r"\findingfield{Condition}{" + tex(finding.explanation) + "}",
                r"\findingfield{Samples}{"
                + tex(
                    f"{len(finding.sample_ids)}/{samples}: " + ", ".join(finding.sample_ids)
                    if finding.sample_ids
                    else "Benchmark-wide finding; no individual sample count claimed."
                )
                + "}",
                r"\findingfield{Evidence}{" + evidence(finding.evidence) + "}",
                r"\end{finding}",
            ]
        parts.append(r"\end{dimensionreview}")
    parts += [
        r"\end{reportpart}\auditpart{Scoring Criteria}{criteria}\auditdestination{criteria}",
        r"\input{template/sections/ScoringCriteria}\auditpartsend\end{maincontentstyled}",
    ]
    wrapper = (upstream / "reports/Example/Example_Audit.tex").read_text()
    for command, value in {
        "reportno": manifest["files"]["findings.json"]["sha256"][:12],
        "evalname": findings.subject.get("task", "Evaluation"),
        "evalscore": ratings["overall"].label,
        "commitref": str(findings.subject.get("revision") or "Unknown")[:12],
    }.items():
        wrapper = re.sub(
            r"\\" + command + r"\{[^}]*\}",
            lambda _, command=command, value=value: "\\" + command + "{" + tex(value) + "}",
            wrapper,
        )
    return wrapper, "\n".join(parts)


async def build(root: Path, bundle: Path) -> Path:
    findings, manifest = read_bundle(bundle)
    try:
        prose = Prose.model_validate_json(await sandbox().read_file("/workspace/report/prose.json"))
    except FileNotFoundError as ex:
        raise ToolError(
            "Write /workspace/report/prose.json using the latex-publishing skill"
        ) from ex
    upstream = await asyncio.to_thread(template)
    wrapper, content = source(findings, manifest, prose, upstream)
    output = root / "rendered-latex"
    if output.exists():
        shutil.rmtree(output)
    output.mkdir()
    pin = json.loads(Path(__file__).with_name("template.json").read_text())
    for name in pin["files"]:
        if name.startswith("template/"):
            target = output / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(upstream / name, target)
    shutil.copyfile(upstream / ".latexmkrc", output / ".latexmkrc")
    (output / "report.tex").write_text(wrapper)
    (output / "Content.tex").write_text(content)
    (output / "prose.json").write_bytes(encoded(prose.model_dump()))
    (output / "template-provenance.json").write_bytes(
        (Path(__file__).with_name("template.json")).read_bytes()
    )
    command = [
        "latexmk",
        "-pdf",
        "-interaction=nonstopmode",
        "-halt-on-error",
        "-no-shell-escape",
        "report.tex",
    ]
    if image := os.environ.get("INSPECT_AUDIT_LATEX_IMAGE"):
        command = [
            "docker",
            "run",
            "--rm",
            "--network=none",
            "--entrypoint",
            "latexmk",
            "-v",
            f"{output}:/report",
            "-w",
            "/report",
            image,
            *command[1:],
        ]
    elif not shutil.which("latexmk"):
        raise ToolError(
            "Install latexmk and TeX on the publishing host, or set INSPECT_AUDIT_LATEX_IMAGE to the publishing image"
        )
    result = await subprocess(command, cwd=str(output), timeout=300)
    if not result.success:
        raise ToolError(
            f"LaTeX compilation failed: {str(result.stdout)[-4000:]} {str(result.stderr)[-1000:]}"
        )
    for suffix in ("aux", "log", "out", "fls", "fdb_latexmk", "synctex.gz"):
        (output / ("report." + suffix)).unlink(missing_ok=True)
    return output


@tool(parallel=False)
def preview_report(root: Path) -> Tool:
    async def execute() -> str:
        """Compile the GL audit template from frozen findings and prose-only JSON."""
        from .._findings import restore_bundle

        output = await build(root, await restore_bundle(root))
        await sandbox().write_file(
            "/workspace/report/report.pdf", (output / "report.pdf").read_bytes()
        )
        return "Preview ready at /workspace/report/report.pdf. Revise prose.json, then publish_document."

    return execute


def publisher() -> Publisher:
    return Publisher(
        skills=[str(Path(__file__).parent / "skills/latex-publishing")],
        build=build,
        tools=lambda root: [preview_report(root)],
        validate=validate,
    )
