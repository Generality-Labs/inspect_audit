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
from ._render import box_has, render_in_box, reported, tree
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


# What pdfLaTeX with T1 fonts (the template's default engine) can set from UTF-8: Latin
# text and common typographic punctuation. Anything else (box drawing, emoji, CJK)
# stops the whole report (breadth round, docvqa: a progress bar's U+2588 in a recorded
# error). It is shown as "?" instead: the report says less, but it is produced.
_SETTABLE = {
    *(chr(c) for c in range(0x20, 0x7F)),
    *(chr(c) for c in range(0xA0, 0x180)),
    *"\n\t\u2013\u2014\u2018\u2019\u201c\u201d\u2022\u2026\u20ac\u2032\u2033",
}


def _settable(text: str) -> str:
    return "".join(c if c in _SETTABLE else "?" for c in text)


def tex(value: object) -> str:
    value = _settable(str(value))
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
        "<": r"\textless{}",
        ">": r"\textgreater{}",
        "|": r"\textbar{}",
    }

    def word(w: str) -> str:
        # names, paths and hashes have no hyphenation points: they may break after a
        # separator, and a long one anywhere, rather than run off the page
        if len(w) > 30:
            return r"\allowbreak{}".join(substitutions.get(c, c) for c in w)
        return "".join(substitutions.get(c, c) + (r"\allowbreak{}" if c in "/_" else "") for c in w)

    return "".join(word(w) for w in re.split(r"(\s+)", str(value))).replace("\n\n", "\\par ")


# inspect_audit's layout over the pinned template, loaded after auditframework
STYLE = Path(__file__).with_name("inspectaudit.sty")

# assessment colours, as the template's \assess macros use them
COLOURS = {
    "Critical": "ratingDarkRed",
    "Major": "ratingRed",
    "Minor": "ratingAmber",
    "None": "ratingGreen",
    "Not applicable": "ratingGrey",
    "Not assessed": "ratingSlate",
}


RULE = (
    "ratings.overall takes a grade ({grades}); every dimension (A-I) and criterion "
    "(e.g. C.3) takes an assessment ({assessments}), never a letter grade"
)


def validate(findings: Findings) -> None:
    registry = json.loads(REGISTRY.read_text())
    definitions, grades, assessments = (
        registry["dimensions"],
        registry["grades"],
        registry["assessments"],
    )
    rule = RULE.format(grades=", ".join(grades), assessments=", ".join(assessments))
    ratings = findings.auditor.ratings
    if "overall" not in ratings or ratings["overall"].label not in grades:
        raise ValueError(
            "LaTeX publication needs ratings.overall, with its explanation and evidence, "
            f"before submission: {rule}"
        )
    for key, rating in ratings.items():
        if key == "overall":
            continue
        if key not in definitions:
            raise ValueError(f"Unknown framework criterion {key!r}: IDs are A-I and A.1, C.3, ...")
        if rating.label not in assessments:
            raise ValueError(f"{key}={rating.label} is not an assessment: {rule}")
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
    if unknown := sorted(set(prose.dimensions) - set(dimensions)):
        raise ValueError(
            f"prose.json dimensions are keyed A-I only, not {', '.join(unknown)}: "
            "a sub-criterion's text belongs under its letter"
        )
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

    # Evidence sources (log paths, files, URLs) are numbered references to an appendix,
    # in order of first citation, rather than paths inline after every observation.
    sources: dict[str, int] = {}

    def cite(source: str) -> str:
        return str(sources.setdefault(source, len(sources) + 1))

    def evidence(label: str, records: list[Evidence]) -> str:
        if not records:
            return ""
        return (
            r"\iaevidence{"
            + label
            + "}{"
            + "".join(r"\iaitem{" + tex(e.observed) + "}{" + cite(e.source) + "}" for e in records)
            + "}"
        )

    def field(name: str, value: object) -> str:
        return r"\dsfield{" + name + "}{" + tex(value) + "}\n"

    def subject_field(name: str, value: object) -> str:
        # unset metadata is left out, not shown as "Not recorded"
        if value is None or value == "" or value == [] or value == {}:
            return ""
        if isinstance(value, list) and all(isinstance(v, str) and len(v) < 60 for v in value):
            # identifiers: one wrapped line, not one line each
            return field(tex(name), ", ".join(value))
        if isinstance(value, list):
            return (
                r"\iafieldlist{"
                + tex(name)
                + "}{"
                + "".join(
                    r"\item " + tex(json.dumps(v) if isinstance(v, (dict, list)) else v)
                    for v in value
                )
                + "}\n"
            )
        if isinstance(value, dict):
            value = ", ".join(f"{k}={json.dumps(v)}" for k, v in value.items())
        return field(tex(name), value)

    def box(title: str, content: str) -> str:
        return r"\begin{dsbox}[breakable]{" + title + "}\n" + content + "\n" + r"\end{dsbox}" + "\n"

    def gist(text: str) -> str:
        # the scorecard row is the template's "[one-line description]", in a box that
        # cannot break across pages; the full explanation is in the dimension's part
        return tex(re.split(r"(?<=\.)\s", text.strip(), maxsplit=1)[0])

    parts = []
    for letter, definition in dimensions.items():
        contributions = " ".join(
            r"\iaissue{"
            + key
            + "}{"
            + tex(definitions[key]["name"])
            + "}{"
            + COLOURS[rating.label]
            + "}"
            for key, rating in ratings.items()
            if key.startswith(letter + ".") and rating.label in ("Minor", "Major", "Critical")
        ) or (gist(ratings[letter].explanation) if letter in ratings else explanation(letter))
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
            + field("Report no.", manifest["files"]["findings.json"]["sha256"][:12])
            + field("Prepared by", "Automated audit")
            + field(
                "Revision",
                "Generated from the immutable submitted findings; this publication "
                "preserves their checksum.",
            ),
        ),
        box("Executive Summary", tex(findings.auditor.summary)),
        # the overall grade's evidence; the scorecard keeps to its one page
        box("Headline evidence", evidence("", ratings["overall"].evidence))
        if ratings["overall"].evidence
        else "",
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
        placeholder: tex(findings.subject[key])
        for placeholder, key in replacements.items()
        if findings.subject.get(key) not in (None, "")
    }
    # Fields nothing records (the Example's requirements, unset subject metadata) are
    # left out rather than shown as "Not recorded", and a box left empty goes with them.
    scorecard = re.sub(
        r"(?m)^[ \t]*\\dsfield\{[^}]*\}\{\[(?!Headline)[^\n]*\]\}[ \t]*\n",
        lambda match: match[0] if any(key in match[0] for key in values) else "",
        scorecard,
    )
    scorecard = re.sub(
        r"\\noindent\s*\\begin\{dsbox\}(\[[^\]]*\])?\{[^}]*\}\s*\\end\{dsbox\}", "", scorecard
    )
    # One pass over template placeholders: inserted findings are never reinterpreted.
    scorecard = re.sub(
        "|".join([r"\[Headline assessment:.*?\]", *(re.escape(key) for key in values)]),
        lambda match: (
            tex(ratings["overall"].explanation)
            if match[0].startswith("[Headline assessment:")
            else values[match[0]]
        ),
        scorecard,
        flags=re.DOTALL,
    )
    parts.append(scorecard)
    subject = "".join(subject_field(key, value) for key, value in findings.subject.items())
    models = sorted(
        {
            take["model"]
            for checks in rows.values()
            for takes in checks.values()
            for take in takes
            if take.get("model")
        }
    )
    log_references = manifest.get("log_references", [])
    parts += [
        r"\auditpart{Eval Datasheet}{datasheet}\begin{reportpart}{Eval Datasheet}{datasheet}{sec:datasheet}\auditdestination{datasheet}",
        box("Overview", tex(prose.datasheet)) if prose.datasheet else "",
        box("Benchmark", subject),
        r"\end{reportpart}",
        r"\auditpart{Audit Setup}{methodology}\begin{reportpart}{Audit Setup}{methodology}{sec:methodology}\auditdestination{methodology}",
        box("Scope of Report", tex(prose.setup)),
        box(
            "Run Setup",
            field("Samples", f"{investigated}/{samples} selected samples have investigator results")
            + field("Jobs", len(manifest.get("jobs", [])))
            + (
                field("Logs", f"{len(log_references)} referenced by address and SHA-256 (Appendix)")
                if log_references
                else ""
            ),
        ),
        box("Models", tex(", ".join(models))) if models else "",
        box("Limitations", tex("\n\n".join(findings.auditor.limitations) or "None recorded.")),
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
            + "}"
            for code, row in definitions.items()
            if code.startswith(letter + ".")
        )
        supporting = "".join(
            evidence(
                (
                    r"\textcolor{brandTeal}{"
                    + code
                    + r"}~"
                    + r"\glupfirst{"
                    + tex(row["name"])
                    + "}"
                    if "." in code
                    else "Overall"
                ),
                ratings[code].evidence,
            )
            for code, row in definitions.items()
            if (code == letter or code.startswith(letter + ".")) and code in ratings
        )
        parts += [
            r"\begin{dimensionreview}{" + key + "}",
            r"\dimensionoverview{" + key + "}{" + explanation(letter) + "}{" + contributions + "}",
            tex(prose.dimensions.get(letter, "")),
            (r"\iaevidenceheading " + supporting) if supporting else "",
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
                evidence("Evidence:", finding.evidence),
                r"\end{finding}",
            ]
        parts.append(r"\end{dimensionreview}")
    parts.append(r"\end{reportpart}")
    if sources or log_references:
        parts += [
            r"\auditpart{Appendix: Sources}{sources}\begin{reportpart}{Appendix: Sources}{sources}{sec:sources}\auditdestination{sources}",
            box(
                "Evidence sources",
                r"\footnotesize "
                + "".join(r"\iasource{" + str(n) + "}{" + tex(s) + "}" for s, n in sources.items()),
            )
            if sources
            else "",
            box(
                "Log references",
                r"\footnotesize "
                + "".join(
                    r"\iasource{L"
                    + str(n)
                    + "}{"
                    + tex(ref["address"])
                    + r"\newline SHA-256 "
                    + tex(ref["sha256"])
                    + "}"
                    for n, ref in enumerate(log_references, 1)
                ),
            )
            if log_references
            else "",
            r"\end{reportpart}",
        ]
    parts += [
        r"\auditpart{Scoring Criteria}{criteria}\auditdestination{criteria}",
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
    wrapper = wrapper.replace(
        r"\usepackage{auditframework}", r"\usepackage{auditframework}\usepackage{inspectaudit}", 1
    )
    content = "\n".join(parts)
    if sources or log_references:
        content = content.replace(
            r"\auditcontentsrow{criteria}",
            r"\auditcontentsrow{sources}{Appendix: Sources}"
            "\n"
            r"\auditcontentsrow{criteria}",
            1,
        )
    return wrapper, content


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
    shutil.copyfile(STYLE, output / "template" / STYLE.name)
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
    if await box_has("command -v latexmk"):
        # the auditor's box carries TeX wherever the audit runs; the host may not
        files = tree(output)
        compiled = await render_in_box(
            {f"src/{name}": data for name, data in files.items()},
            [
                "sh",
                "-c",
                f"cd src && {' '.join(command)} && mkdir -p ../out && cp report.pdf ../out/",
            ],
            "out",
        )
        (output / "report.pdf").write_bytes(compiled["report.pdf"])
        return output
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
            "No TeX to compile the report: use an auditor image with latexmk, install "
            "latexmk on the Inspect host, or set INSPECT_AUDIT_LATEX_IMAGE"
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

        with reported("Preview"):
            output = await build(root, await restore_bundle(root))
        await sandbox().write_file(
            "/workspace/report/report.pdf", (output / "report.pdf").read_bytes()
        )
        return "Preview ready at /workspace/report/report.pdf. Revise prose.json, then publish_document."

    return execute


async def preflight() -> None:
    """Refuse to start an audit whose report nothing here can compile."""
    if await box_has("command -v latexmk && command -v pdflatex"):
        return
    if image := os.environ.get("INSPECT_AUDIT_LATEX_IMAGE"):
        if not shutil.which("docker"):
            raise RuntimeError(
                f"INSPECT_AUDIT_LATEX_IMAGE is {image!r}, but there is no Docker on the Inspect host"
            )
        result = await subprocess(["docker", "image", "inspect", image], timeout=60)
        if not result.success:
            raise RuntimeError(
                f"The LaTeX image {image!r} is not available on the Inspect host; build or pull it first"
            )
        return
    if not (shutil.which("latexmk") and shutil.which("pdflatex")):
        raise RuntimeError(
            "Nothing can compile the report: use an auditor image with latexmk, install latexmk "
            "and pdflatex on the Inspect host, or set INSPECT_AUDIT_LATEX_IMAGE"
        )


def publisher() -> Publisher:
    return Publisher(
        skills=[str(Path(__file__).parent / "skills/latex-publishing")],
        build=build,
        tools=lambda root: [preview_report(root)],
        validate=validate,
        preflight=preflight,
    )
