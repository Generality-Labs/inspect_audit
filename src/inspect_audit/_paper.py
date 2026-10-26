"""The benchmark's paper as a file: a local path, or a URL downloaded now."""

import re
import urllib.request
from functools import cache
from pathlib import Path


@cache
def paper_file(paper: str) -> tuple[str, bytes]:
    """(file name, bytes) for a paper given as a path or URL; an arXiv abstract becomes its PDF.

    Raises OSError when it cannot be read or downloaded.
    """
    local = Path(paper).expanduser()
    if local.is_file():
        return local.name, local.read_bytes()
    if not paper.startswith(("http://", "https://")):
        raise OSError(f"not a file or URL: {paper}")
    url = re.sub(r"arxiv\.org/abs/([^v?#]+)(v\d+)?", r"arxiv.org/pdf/\1\2", paper)
    name = Path(url.split("?", 1)[0].rstrip("/")).name or "paper"
    if not name.lower().endswith(".pdf"):
        name += ".pdf"
    request = urllib.request.Request(url, headers={"User-Agent": "inspect_audit"})
    with urllib.request.urlopen(request, timeout=60) as response:
        return name, response.read()
