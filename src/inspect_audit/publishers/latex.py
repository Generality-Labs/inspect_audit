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
from .._framework import REGISTRY, stage_framework
from .._framework import SKILL as FRAMEWORK_SKILL
from .._publishing import Publisher
from .._verdicts import Evidence
from ._excerpt import MARKER, Excerpt, Quote, plain, resolve_all, viewer_base
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
    # verbatim evidence, declared by where it is and placed with {{excerpt:<id>}}
    excerpts: list[Excerpt] = Field(default_factory=list)


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


_TEX = {
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


def tex(value: object) -> str:
    value = _settable(str(value))
    substitutions = _TEX

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


# what pdfLaTeX's T1 typewriter font would otherwise set as ligatures or curly quotes
_CODE = {
    "'": r"\textquotesingle{}",
    "`": r"\textasciigrave{}",
    '"': r"\textquotedbl{}",
    "-": "-{}",
    ",": ",{}",
    " ": "\\ ",
}


def code(line: str) -> str:
    """One recorded line in the typewriter font: every space kept, long runs breakable."""
    line = line.rstrip()
    escaped = []
    for word in re.split(r"( +)", _settable(line)):
        if word.startswith(" "):
            escaped.append("\\ " * len(word))
            continue
        long = len(word) > 30
        escaped.append(
            "".join(
                (_CODE.get(c) or _TEX.get(c, c)) + (r"\allowbreak{}" if long or c in "/_" else "")
                for c in word
            )
        )
    return "".join(escaped)


def _code_line(line: str) -> str:
    # a line too long for the box continues under its own indentation, a little further in
    indent = len(line) - len(line.lstrip(" "))
    return r"\iaxline{" + str(indent) + "}{" + code(line) + "}"


# a line of prose that starts a list item, a heading or a quotation keeps its own line
_ITEM = re.compile(r"^\s*(?:[-*+\u2022]\s|\d{1,3}[.)]\s|#{1,6}\s|>\s?)")
_INLINE_CODE = re.compile(r"`([^`\n]+)`")


def _prose_line(line: str) -> str:
    """Prose in the text font, with `inline code` set as code."""
    parts = _INLINE_CODE.split(line)
    return "".join(
        tex(part) if i % 2 == 0 else r"\iatt{" + code(part) + "}" for i, part in enumerate(parts)
    )


def prose(lines: list[str]) -> str:
    """Recorded prose, set as a reader reads it.

    Lines wrapped by the source join into paragraphs; a blank line ends one, and a list
    item or heading starts its own line. A fenced block (```) is kept line for line.
    """
    out: list[str] = []
    paragraph: list[str] = []
    fenced = False

    def flush() -> None:
        if paragraph:
            out.append(r"\iaqpar{" + " ".join(paragraph) + "}")
            paragraph.clear()

    for line in lines:
        if line.strip().startswith("```"):
            flush()
            fenced = not fenced
            continue
        if fenced:
            out.append(_code_line(line).replace(r"\iaxline", r"\iaqcode", 1))
        elif not line.strip():
            flush()
        else:
            if _ITEM.match(line):
                flush()
            paragraph.append(_prose_line(line.strip()))
    flush()
    return "".join(out)


def _href(url: str, label: str) -> str:
    """A link inside a macro's argument: its URL's own # and % escaped for hyperref."""
    url = url.replace("~", "%7E").replace("%", r"\%").replace("#", r"\#")
    return r"\href{" + url + "}{" + label + "}"


def excerpt_box(quote: Quote) -> str:
    """A quotation: its caption (the link to its sample), then its lines.

    Prose is set as a block quotation in the text font; recorded output as recorded.
    """
    caption = tex(quote.caption)
    if quote.link:
        caption = _href(quote.link, caption)
    if quote.kind == "text":
        body, environment = prose(quote.lines), "iaquote"
    else:
        body, environment = "".join(_code_line(line) for line in quote.lines), "iaexcerpt"
    if quote.elided:
        body += r"\iaxelided{" + str(quote.elided) + "}"
    return r"\begin{" + environment + "}{" + caption + "}" + body + r"\end{" + environment + "}"


_PRODUCED = re.compile(r"^(\d+) of (\d+) logs produced by (\S+) (\S+); (.*)$")


def _version_key(version: str) -> list[tuple[int, str]]:
    return [(int(p), "") if p.isdigit() else (-1, p) for p in re.split(r"[.+-]", version)]


def logs_in_words(logs: list[Any], provenance: object) -> str:
    """The supplied logs as a reader takes them in: how many, and what recorded them.

    Their addresses are in the published findings; a list of ids tells a reader nothing.
    """
    said = f"{len(logs)} supplied" if len(logs) != 1 else "1 supplied"
    notes = [str(p) for p in provenance] if isinstance(provenance, list) else []
    matched = [_PRODUCED.match(n) for n in notes]
    if notes and all(matched):
        found = [m for m in matched if m]
        packages, tails = {m[3] for m in found}, {m[5] for m in found}
        if len(packages) == 1 and len(tails) == 1:
            package = packages.pop()
            builds = sorted(found, key=lambda m: _version_key(m[4]))
            counts = ", ".join(f"{m[4]} ({m[1]})" for m in builds)
            tail = tails.pop().removeprefix(f"{package} ")
            return f"{said}, recorded by {package} {counts}; {package} {tail}"
    return "; ".join([said, *notes])


def source(
    findings: Findings,
    manifest: dict[str, Any],
    prose: Prose,
    upstream: Path,
    quotes: dict[str, Quote] | None = None,
) -> tuple[str, str]:
    """Generate all template structure; prose values are escaped literal text.

    `quotes` are the prose's excerpts as resolved from their sources (`resolve_all`).
    """
    validate(findings)
    quotes = quotes or {}
    if missing := [e.id for e in prose.excerpts if e.id not in quotes]:
        raise ValueError(f"excerpts {', '.join(missing)} were not resolved from their sources")
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

    # Evidence is printed as observed. Where each piece was found (log paths, files,
    # hashes) is kept in the published findings, not printed: a reader cannot use it.
    placed: list[str] = []

    def text(value: str) -> str:
        """Prose: escaped text, with each {{excerpt:<id>}} replaced by its quotation box."""
        parts = MARKER.split(value)
        out = [tex(parts[0])]
        for name, after in zip(parts[1::2], parts[2::2], strict=True):
            if name not in quotes:
                raise ValueError(
                    f"prose places excerpt {name}, but no excerpt with that id is declared"
                )
            if name in placed:
                raise ValueError(f"excerpt {name} is placed twice; place each excerpt once")
            placed.append(name)
            out += [r"\par", excerpt_box(quotes[name]), tex(after.strip())]
        return "".join(out)

    def evidence(label: str, records: list[Evidence]) -> str:
        if not records:
            return ""
        return (
            r"\iaevidence{"
            + label
            + "}{"
            + "".join(r"\item " + tex(e.observed) for e in records)
            + "}"
        )

    def field(name: str, value: object) -> str:
        return r"\dsfield{" + name + "}{" + tex(value) + "}\n"

    def subject_field(name: str, value: object) -> str:
        # unset metadata is left out, not shown as "Not recorded"
        if value is None or value == "" or value == [] or value == {}:
            return ""
        if name == "revision" and isinstance(findings.subject.get("repo"), str):
            return ""  # the repository links to it
        if name == "repo" and isinstance(value, str) and value.startswith("https://"):
            revision = findings.subject.get("revision")
            target = (
                value.rstrip("/") + "/tree/" + str(revision)
                if revision and "github.com/" in value
                else value
            )
            label = re.sub(r"^https://(www\.)?(github\.com/)?", "", value).rstrip("/")
            return (
                r"\dsfield{repo}{"
                + _href(target, tex(label))
                + (r", at the audited commit" if revision else "")
                + "}\n"
            )
        if name == "supplied_logs" and isinstance(value, list):
            return field("logs", logs_in_words(value, findings.subject.get("logs_provenance")))
        if name == "logs_provenance" and isinstance(findings.subject.get("supplied_logs"), list):
            return ""  # said with the logs
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
        box("Overview", text(prose.datasheet)) if prose.datasheet else "",
        box("Benchmark", subject),
        r"\end{reportpart}",
        r"\auditpart{Audit Setup}{methodology}\begin{reportpart}{Audit Setup}{methodology}{sec:methodology}\auditdestination{methodology}",
        box("Scope of Report", text(prose.setup)),
        box(
            "Run Setup",
            field("Samples", f"{investigated}/{samples} selected samples have investigator results")
            + field("Jobs", len(manifest.get("jobs", [])))
            + (
                field(
                    "Logs",
                    f"{len(log_references)} recorded logs read; their addresses and "
                    "checksums are kept with the published findings",
                )
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
            text(prose.dimensions.get(letter, "")),
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
    if unplaced := [name for name in quotes if name not in placed]:
        raise ValueError(
            f"excerpts {', '.join(unplaced)} are declared but not placed: put "
            "{{excerpt:<id>}} where each belongs in the prose, or remove it"
        )
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
    # read where they are recorded: the bundle, the audit's inputs, or a log's address
    viewer = await asyncio.to_thread(viewer_base) if prose.excerpts else None
    quotes = await asyncio.to_thread(resolve_all, prose.excerpts, bundle, manifest, root, viewer)
    wrapper, content = source(findings, manifest, prose, upstream, quotes)
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
    if quotes:
        # what the report quotes, as resolved, beside where it was read from and its link
        (output / "excerpts.txt").write_text("\n\n".join(plain(q) for q in quotes.values()) + "\n")
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
        quoted = ""
        if (output / "excerpts.txt").exists():
            await sandbox().write_file(
                "/workspace/report/excerpts.txt", (output / "excerpts.txt").read_text()
            )
            quoted = (
                " The excerpts as quoted, with their sources and links, are in "
                "/workspace/report/excerpts.txt."
            )
        return (
            "Preview ready at /workspace/report/report.pdf."
            + quoted
            + " Revise prose.json, then publish_document."
        )

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
        # the rubric the ratings were made against, to explain them in plain words
        skills=[
            str(Path(__file__).parent / "skills/latex-publishing"),
            str(FRAMEWORK_SKILL.parent),
        ],
        build=build,
        tools=lambda root: [preview_report(root)],
        validate=validate,
        preflight=preflight,
        stage=stage_framework,
    )
