"""The report is drawn in the auditor's box, so the Inspect host needs no TeX or browser."""

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_helpers.box import local_box

from inspect_audit.publishers import _render


def test_files_go_in_and_outputs_come_back() -> None:
    with local_box():
        back = asyncio.run(
            _render.render_in_box(
                {"in/a.txt": b"hello"},
                ["sh", "-c", "mkdir out && tr a-z A-Z < in/a.txt > out/b.txt"],
                "out",
            )
        )
    assert back == {"b.txt": b"HELLO"}


def test_a_renderer_failure_says_why() -> None:
    from inspect_ai.tool import ToolError

    with local_box(), pytest.raises(ToolError, match="no TeX here"):
        asyncio.run(_render.render_in_box({}, ["sh", "-c", "echo no TeX here >&2; exit 1"], "out"))


def test_no_box_means_no_box_tools() -> None:
    # outside a sample there is no sandbox: the publishers fall back to the host
    assert asyncio.run(_render.box_has("true")) is False


@pytest.mark.publishing
def test_the_gl_renderer_draws_a_pdf_in_the_box(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    pytest.importorskip("playwright")
    from inspect_audit.publishers.gl import RENDERER

    page = "<!doctype html><html><body><h1>Synthetic report</h1></body></html>"
    with local_box():
        back = asyncio.run(
            _render.render_in_box(
                {
                    "site/report.html": page.encode(),
                    "selected.json": json.dumps([]).encode(),
                    "gl_render.py": RENDERER.read_bytes(),
                },
                ["python", "gl_render.py", "{dir}"],
                "out",
            )
        )
    assert back["report.pdf"].startswith(b"%PDF")
    assert "Synthetic report" in back["report.html"].decode()


class ScriptedBox:
    """A box that answers the browser probe with `has`, and records what it was asked to run."""

    def __init__(self, has: bool, install_ok: bool = True) -> None:
        self.has, self.install_ok, self.ran = has, install_ok, []

    async def exec(self, cmd: list[str], cwd: str | None = None, timeout: int | None = None):
        self.ran.append(cmd[-1])
        if cmd[-1] == _render.BROWSER:
            return SimpleNamespace(success=self.has, stdout="", stderr="")
        return SimpleNamespace(success=self.install_ok, stdout="", stderr="apt: no network")


def test_the_browser_is_installed_in_the_box_only_when_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    present = ScriptedBox(has=True)
    monkeypatch.setattr(_render, "sandbox", lambda: present)
    assert asyncio.run(_render.ensure_browser())
    assert present.ran == [_render.BROWSER]

    missing = ScriptedBox(has=False)
    monkeypatch.setattr(_render, "sandbox", lambda: missing)
    assert asyncio.run(_render.ensure_browser())
    assert "playwright install --with-deps chromium" in missing.ran[-1]
    assert f"playwright=={_render.PLAYWRIGHT}" in missing.ran[-1]


def test_a_box_that_cannot_install_the_browser_says_why(monkeypatch: pytest.MonkeyPatch) -> None:
    from inspect_ai.tool import ToolError

    monkeypatch.setattr(_render, "sandbox", lambda: ScriptedBox(has=False, install_ok=False))
    with pytest.raises(ToolError, match="no network"):
        asyncio.run(_render.ensure_browser())


def test_no_box_means_draw_on_the_host() -> None:
    assert asyncio.run(_render.ensure_browser()) is False


def test_the_box_pins_the_playwright_the_package_locks() -> None:
    lock = (Path(__file__).parents[1] / "uv.lock").read_text()
    assert f'name = "playwright"\nversion = "{_render.PLAYWRIGHT}"' in lock
