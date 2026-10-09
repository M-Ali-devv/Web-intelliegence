"""Render a page only when HTTP returned a JavaScript shell.

Playwright is imported lazily. If it is not installed, or the browser fails,
the caller keeps the HTTP result and continues.
"""

from __future__ import annotations

from app.crawler import config
from app.crawler.guard import GuardError, assert_public_url

_SKIPPED_TYPES = {"image", "media", "font"}


async def render_page(url: str) -> bytes | None:
    """Return rendered HTML, or None when the browser cannot be used."""
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        return None
    try:
        assert_public_url(url)
    except GuardError:
        return None

    try:
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True)
            try:
                page = await browser.new_page()

                async def _route(route):
                    if route.request.resource_type in _SKIPPED_TYPES:
                        await route.abort()
                    else:
                        await route.continue_()

                await page.route("**/*", _route)
                await page.goto(
                    url,
                    wait_until="domcontentloaded",
                    timeout=int(config.BROWSER_TIMEOUT * 1000),
                )
                return (await page.content()).encode("utf-8")
            finally:
                await browser.close()
    except Exception:
        return None
