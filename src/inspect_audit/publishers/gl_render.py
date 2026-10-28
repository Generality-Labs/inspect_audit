"""Draw a GL report in Chromium: run as a script where the browser is, not imported.

    python gl_render.py DIR

DIR holds `site/` (report.html and its assets, served from one invented origin so
nothing else loads) and `selected.json` (the figure names drawn). Writes report.pdf,
preview.png, one SVG per figure and the script-free report.html to `DIR/out/`.
Exits nonzero with the reason on stderr when a component or the browser fails.
"""

import asyncio
import json
import sys
from pathlib import Path

from playwright.async_api import Error, Route, async_playwright

PREFIX = "https://publication.invalid/"


async def render(root: Path) -> None:
    site, out = root / "site", root / "out"
    out.mkdir(exist_ok=True)
    selected = json.loads((root / "selected.json").read_text())
    errors: list[str] = []
    async with async_playwright() as playwright:
        try:
            browser = await playwright.chromium.launch()
        except Error as ex:
            raise SystemExit(
                "Chromium could not start: run python -m playwright install --with-deps chromium. "
                + str(ex)
            ) from ex
        try:
            page = await browser.new_page(viewport={"width": 1000, "height": 900})
            page.on("pageerror", lambda error: errors.append(str(error)))

            async def route_request(route: Route) -> None:
                url = route.request.url
                file = site / url.removeprefix(PREFIX)
                if not url.startswith(PREFIX) or not file.is_file():
                    await route.abort()
                    return
                await route.fulfill(
                    body=file.read_bytes(),
                    content_type="application/javascript" if url.endswith(".js") else "text/html",
                )

            await page.route("**/*", route_request)
            await page.goto(PREFIX + "report.html")
            if selected:
                await page.wait_for_function("window.figuresReady === true", timeout=30000)
            if errors:
                raise SystemExit("GL component failed: " + "; ".join(errors))
            await page.evaluate("document.fonts.ready")
            await page.pdf(path=str(out / "report.pdf"), format="A4", print_background=True)
            await page.screenshot(path=str(out / "preview.png"), full_page=True)
            for name in selected:
                node = page.locator(f'[data-figure="{name}"] svg').first
                (out / f"{name}.svg").write_text(await node.evaluate("node => node.outerHTML"))
            await page.evaluate(
                "document.querySelectorAll('script').forEach(node => node.remove())"
            )
            (out / "report.html").write_text(await page.content())
        except Error as ex:
            raise SystemExit("GL rendering failed: " + ("; ".join(errors) or str(ex))) from ex
        finally:
            await browser.close()


if __name__ == "__main__":
    asyncio.run(render(Path(sys.argv[1])))
