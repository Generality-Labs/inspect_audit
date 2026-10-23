"""`view_image`: look at an image in a box, rasterising SVGs there first."""

import base64
from io import BytesIO
from pathlib import Path

from inspect_ai.model import ContentImage
from inspect_ai.tool import Tool, ToolError, ToolResult, tool
from inspect_ai.util import sandbox
from PIL import Image


@tool
def view_image(service: str | None = None) -> Tool:
    """View an image in `service`'s box (the default box when None)."""

    async def execute(path: str) -> ToolResult:
        """Look at an image: an item's media, or a figure you have drawn.

        Reading the bytes with bash establishes nothing; call this to see the picture.
        Investigating an item, the media is staged under `/audit/media/` and `sample.json`
        gives the path. Writing a report, this is how you check a figure before you
        publish it, from the file your script wrote under
        `/workspace/report/evidence/`.

        Args:
            path: The image's path in this box.
        """
        try:
            data = await sandbox(service).read_file(path, text=False)
        except Exception as ex:
            raise ToolError(f"Could not read {path!r}: {type(ex).__name__}: {ex}") from None
        if not isinstance(data, bytes):
            raise ToolError(f"{path!r} did not read back as bytes.")
        if Path(path).suffix.lower() == ".svg" or b"<svg" in data[:4096]:
            # Render inside the sandbox: SVGs can reference other files. Keep the
            # vector original for publication; only the model preview is rasterized.
            try:
                result = await sandbox(service).exec(
                    [
                        "python",
                        "-c",
                        "import base64, subprocess, sys; "
                        "p = subprocess.run(['rsvg-convert', '--', sys.argv[1]], "
                        "capture_output=True, check=True, timeout=45); "
                        "print(base64.b64encode(p.stdout).decode())",
                        path,
                    ],
                    timeout=60,
                )
                if not result.success:
                    raise ValueError(result.stderr[-2000:])
                data = base64.b64decode(result.stdout.strip(), validate=True)
            except Exception as ex:
                raise ToolError(
                    f"Could not preview SVG {path!r}: {ex}. "
                    "Install librsvg2-bin in the sandbox or export a PNG and view that."
                ) from None
        try:
            with Image.open(BytesIO(data)) as preview:
                image_format = preview.format
                preview.verify()
            mime = {
                "PNG": "image/png",
                "JPEG": "image/jpeg",
                "GIF": "image/gif",
                "WEBP": "image/webp",
            }.get(image_format or "")
            if mime is None:
                raise ValueError(f"unsupported image format {image_format}; export a PNG")
        except Exception as ex:
            raise ToolError(f"Cannot preview {path!r}: {ex}") from None
        encoded = base64.b64encode(data).decode()
        return [ContentImage(image=f"data:{mime};base64,{encoded}")]

    return execute
