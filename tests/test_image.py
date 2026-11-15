"""The image viewer the investigator and the auditor share, on a real box.

The box is Inspect's local sandbox (`local_box`): files are real, and an SVG is drawn
by the real `rsvg-convert`, as in the published boxes.
"""

import base64
import shutil
from io import BytesIO
from pathlib import Path

import anyio
import pytest
from inspect_ai.tool import ToolError
from PIL import Image
from test_helpers.box import local_box

from inspect_audit._image import view_image

needs_rsvg = pytest.mark.skipif(
    shutil.which("rsvg-convert") is None, reason="needs rsvg-convert (librsvg2-bin)"
)


def _png(size: tuple[int, int] = (4, 4)) -> bytes:
    output = BytesIO()
    Image.new("RGB", size, "red").save(output, format="PNG")
    return output.getvalue()


def _shown(result: list) -> Image.Image:  # type: ignore[type-arg]
    data = result[0].image.removeprefix("data:image/png;base64,")
    return Image.open(BytesIO(base64.b64decode(data)))


def test_an_image_is_read_by_its_bytes_not_its_name(tmp_path: Path) -> None:
    (tmp_path / "image.wrong-extension").write_bytes(_png())
    (tmp_path / "broken.png").write_bytes(b"broken image")
    with local_box():
        view = view_image()
        assert _shown(anyio.run(view, str(tmp_path / "image.wrong-extension"))).size == (4, 4)
        with pytest.raises(ToolError, match="Cannot preview"):
            anyio.run(view, str(tmp_path / "broken.png"))
        with pytest.raises(ToolError, match="Could not read"):
            anyio.run(view, str(tmp_path / "missing.png"))


@needs_rsvg
def test_an_svg_is_drawn_in_the_box(tmp_path: Path) -> None:
    svg = tmp_path / "architecture.svg"
    svg.write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" width="20" height="10">'
        '<rect width="20" height="10" fill="blue"/></svg>'
    )
    with local_box():
        assert _shown(anyio.run(view_image(), str(svg))).size == (20, 10)


@needs_rsvg
def test_an_svg_that_cannot_be_drawn_is_a_tool_error(tmp_path: Path) -> None:
    svg = tmp_path / "broken.svg"
    svg.write_text("<svg")
    with local_box(), pytest.raises(ToolError, match="export a PNG"):
        anyio.run(view_image(), str(svg))
