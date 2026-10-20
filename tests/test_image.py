"""The image viewer the auditor and the investigator share."""

from types import SimpleNamespace

import anyio
import pytest
from inspect_ai.tool import ToolError

from inspect_audit import _image as image_module


def test_image_preview_validates_bytes_and_rasterizes_svg(monkeypatch):
    import base64
    from io import BytesIO

    from PIL import Image

    output = BytesIO()
    Image.new("RGB", (4, 4), "red").save(output, format="PNG")
    png = output.getvalue()
    calls = []
    payload = png

    async def read_file(path, text=False):
        return payload

    async def execute(command, timeout):
        calls.append(command)
        return SimpleNamespace(success=True, stdout=base64.b64encode(png).decode(), stderr="")

    monkeypatch.setattr(
        image_module,
        "sandbox",
        lambda service=None: SimpleNamespace(read_file=read_file, exec=execute),
    )
    view = image_module.view_image()
    result = anyio.run(view, "/image.wrong-extension")
    assert result[0].image.startswith("data:image/png;base64,")
    assert not calls
    payload = b'<svg xmlns="http://www.w3.org/2000/svg"/>'
    result = anyio.run(view, "/architecture.svg")
    assert result[0].image == "data:image/png;base64," + base64.b64encode(png).decode()
    assert calls[0][-1] == "/architecture.svg"
    payload = b"broken image"
    with pytest.raises(ToolError, match="Cannot preview"):
        anyio.run(view, "/broken.png")


def test_failed_svg_conversion_is_a_tool_error(monkeypatch):
    async def read_file(path, text=False):
        return b"<svg/>"

    async def execute(command, timeout):
        return SimpleNamespace(success=False, stdout="", stderr="rsvg-convert missing")

    monkeypatch.setattr(
        image_module,
        "sandbox",
        lambda service=None: SimpleNamespace(read_file=read_file, exec=execute),
    )
    with pytest.raises(ToolError, match="export a PNG"):
        anyio.run(image_module.view_image(), "/architecture.svg")
