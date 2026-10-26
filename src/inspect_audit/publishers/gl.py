"""Generality Labs components, rendered to a static HTML report and vector PDF."""

import asyncio
import json
import math
import shutil
import urllib.request
from pathlib import Path
from typing import Any, Literal

from inspect_ai.tool import Tool, ToolError, tool
from inspect_ai.util import StoreModel, sandbox, store_as
from pydantic import Field

from .._findings import FindingsState, encoded, restore_bundle
from .._publishing import Publisher
from ._render import ensure_browser, render_in_box, tree

GL_REVISION = "014b3165403bab23d630c0623acf0ed9d50df445"
ASSETS = {
    "d3.js": "https://cdn.jsdelivr.net/npm/d3@7.9.0/dist/d3.min.js",
    "plot.js": "https://cdn.jsdelivr.net/npm/@observablehq/plot@0.6.17/dist/plot.umd.min.js",
}
COMPONENTS = {"bars": "glBars", "stacked-bars": "glStackedBars", "histogram": "glHistogram"}


class Figures(StoreModel):
    figures: dict[str, Any] = Field(default_factory=dict)


def validate_rows(component: str, rows: Any) -> None:
    if component not in COMPONENTS:
        raise ValueError(f"Unknown GL component: {component}")
    if not isinstance(rows, list) or not rows:
        raise ValueError("Figure data must be a nonempty JSON array")
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("Figure rows must be objects")
        if component == "stacked-bars":
            if (
                not isinstance(row.get("label"), str)
                or not isinstance(row.get("segments"), list)
                or not row["segments"]
            ):
                raise ValueError("Stacked bars require label and nonempty segments")
            for segment in row["segments"]:
                if not isinstance(segment, dict) or not isinstance(segment.get("name"), str):
                    raise ValueError("Segments require names")
                value = segment.get("value")
                if (
                    isinstance(value, bool)
                    or not isinstance(value, (int, float))
                    or not math.isfinite(value)
                    or value < 0
                ):
                    raise ValueError("Segment values must be finite nonnegative numbers")
        else:
            value = row.get("value")
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
            ):
                raise ValueError("Figure values must be finite numbers")
            if component == "bars" and not isinstance(row.get("label"), str):
                raise ValueError("Bars require labels")
            if component == "histogram" and not isinstance(row.get("series"), str):
                raise ValueError("Histogram rows require series names")


@tool(parallel=False)
def figure() -> Tool:
    async def execute(
        name: str,
        component: Literal["bars", "stacked-bars", "histogram"],
        data_path: str,
        caption: str,
        x_label: str,
        sources: list[str],
    ) -> str:
        """Add a predefined GL figure using calculated data from existing evidence.

        Args:
            name: Letters, digits, hyphens or underscores; use {{figure:NAME}} in report.md.
            component: bars, stacked-bars or histogram.
            data_path: Absolute /workspace path to a JSON array calculated from existing evidence.
            caption: Explain the result, counted unit and denominator.
            x_label: Axis label including units.
            sources: Bundle-relative evidence paths, or findings.json, used in the calculation.
        """
        import re

        if store_as(FindingsState).phase != "publishing":
            raise ToolError("Submit findings before creating publication figures")
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", name):
            raise ToolError("Figure name must contain letters, digits, hyphens or underscores")
        if not data_path.startswith("/workspace/") or ".." in Path(data_path).parts:
            raise ToolError("Figure data must be under /workspace")
        known = store_as(FindingsState).manifest.get("files", {})
        if not sources or set(sources) - set(known):
            raise ToolError("Figure sources must name files in the findings manifest")
        try:
            rows = json.loads(await sandbox().read_file(data_path))
            validate_rows(component, rows)
        except (ValueError, OSError) as ex:
            raise ToolError(str(ex)) from ex
        if not caption.strip() or not x_label.strip():
            raise ToolError("Figures need a caption and axis label with units")
        saved = store_as(Figures)
        saved.figures = {
            **saved.figures,
            name: {
                "component": component,
                "data": rows,
                "caption": caption,
                "xLabel": x_label,
                "sources": sources,
            },
        }
        return (
            "Figure "
            + name
            + " registered. Put {{figure:"
            + name
            + "}} on its own line in /workspace/report/report.md, then call preview_report."
        )

    return execute


RENDERER = Path(__file__).with_name("gl_render.py")


def _render_here(work: dict[str, bytes], command: list[str]) -> dict[str, bytes]:
    import subprocess
    import sys
    import tempfile

    try:
        import playwright  # noqa: F401
    except ImportError as ex:
        raise ToolError(
            "No browser to draw the report: use an auditor image with Chromium, or "
            "install inspect-audit[publishing] on the Inspect host"
        ) from ex
    with tempfile.TemporaryDirectory() as tmp:
        for name, data in work.items():
            (Path(tmp) / name).parent.mkdir(parents=True, exist_ok=True)
            (Path(tmp) / name).write_bytes(data)
        result = subprocess.run(
            [sys.executable, *[c.replace("{dir}", tmp) for c in command[1:]]],
            cwd=tmp,
            capture_output=True,
            text=True,
            timeout=300,
            check=False,
        )
        if result.returncode:
            raise ToolError(f"Rendering failed: {result.stderr[-4000:]}")
        return tree(Path(tmp) / "out")


def _assets(root: Path) -> dict[str, bytes]:
    cache = root / "publisher-assets"
    cache.mkdir(exist_ok=True)
    for name, url in ASSETS.items():
        path = cache / name
        if not path.exists():
            with urllib.request.urlopen(url, timeout=60) as response:
                path.write_bytes(response.read())
    return {
        "assets/gl.js": (Path(__file__).parent / "assets/gl.js").read_bytes(),
        **{f"assets/{name}": (cache / name).read_bytes() for name in ASSETS},
    }


async def build(root: Path, bundle: Path | None = None) -> Path:
    """Draw approved components in a browser, then save static HTML, SVG and PDF."""
    import re

    from markdown_it import MarkdownIt

    if bundle is None:
        await restore_bundle(root)
    try:
        prose = await sandbox().read_file("/workspace/report/report.md")
    except FileNotFoundError as ex:
        raise ToolError("Write /workspace/report/report.md before previewing or publishing") from ex
    figures = store_as(Figures).figures
    names = re.findall(r"\{\{figure:([A-Za-z0-9_-]+)\}\}", prose)
    if set(names) - set(figures):
        raise ToolError("Report references an unregistered figure")
    markdown = MarkdownIt("commonmark", {"html": False}).enable("table")
    if any(
        child.type == "image" for token in markdown.parse(prose) for child in (token.children or [])
    ):
        raise ToolError("Use registered GL figure markers for images")
    body = markdown.render(prose)
    body = re.sub(r"<p>\{\{figure:([A-Za-z0-9_-]+)\}\}</p>", r'<div data-figure="\1"></div>', body)
    selected = {name: figures[name] for name in dict.fromkeys(names)}
    resources = await asyncio.to_thread(_assets, root) if selected else {}
    payload = json.dumps(selected).replace("<", "\\u003c")
    script = """
import * as gl from './assets/gl.js';
const figures = PAYLOAD;
const menu = MENU;
for (const [name,f] of Object.entries(figures)) {
  const nodes = document.querySelectorAll('[data-figure="'+name+'"]');
  for (const node of nodes) node.appendChild(gl[menu[f.component]]({Plot:window.Plot}, f.data,
    {caption:f.caption,xLabel:f.xLabel,provenance:'Source: '+f.sources.join(', '),tooltip:false}));
}
window.figuresReady = true;
""".replace("PAYLOAD", payload).replace("MENU", json.dumps(COMPONENTS))
    css = "body{font-family:Arial,sans-serif;color:#1a1919;max-width:740px;margin:40px auto;line-height:1.55}h1,h2,h3{line-height:1.2}figure,[data-figure]{break-inside:avoid}svg{max-width:100%;height:auto}table{border-collapse:collapse}td,th{padding:6px;border-bottom:1px solid #ddd}@page{size:A4;margin:18mm}"
    document = (
        f'<!doctype html><html><head><meta charset="utf-8"><style>{css}</style></head><body>{body}'
    )
    if selected:
        document += (
            '<script src="assets/d3.js"></script><script src="assets/plot.js"></script><script type="module">'
            + script
            + "</script>"
        )
    document += "</body></html>"
    resources["report.html"] = document.encode()
    output = root / "rendered"
    if output.exists():
        shutil.rmtree(output)
    output.mkdir()
    work = {
        **{f"site/{name}": data for name, data in resources.items()},
        "selected.json": json.dumps(list(selected)).encode(),
        "gl_render.py": RENDERER.read_bytes(),
    }
    command = ["python", "gl_render.py", "{dir}"]
    if await ensure_browser():
        drawn = await render_in_box(work, command, "out")
    else:
        # no box at all (a direct call): draw on this host
        drawn = await asyncio.to_thread(_render_here, work, command)
    for name, data in drawn.items():
        (output / name).write_bytes(data)
    (output / "report.md").write_text(prose)
    (output / "figures.json").write_bytes(
        encoded({"gl_revision": GL_REVISION, "figures": selected})
    )
    return output


@tool(parallel=False)
def preview_report(root: Path) -> Tool:
    async def execute() -> str:
        """Build GL components and HTML/PDF; inspect /workspace/report/preview.png."""
        output = await build(root)
        for path in output.iterdir():
            await sandbox().write_file(f"/workspace/report/{path.name}", path.read_bytes())
        return "Preview ready: /workspace/report/preview.png, report.html and report.pdf. Inspect the image, revise prose or figure inputs, then publish_document."

    return execute


def publisher() -> "Publisher":
    return Publisher(
        skills=[str(Path(__file__).parent / "skills/publishing")],
        build=build,
        tools=lambda root: [figure(), preview_report(root)],
    )
