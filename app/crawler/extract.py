"""Turn raw HTML into the clean text stored in Websites.clean_text."""

from __future__ import annotations

import re

from bs4 import BeautifulSoup, Tag

# Boilerplate that never helps classify a business.
DROP_TAGS = [
    "script", "style", "noscript", "svg", "canvas", "iframe", "frame",
    "form", "button", "input", "select", "textarea", "nav", "header",
    "footer", "aside", "template", "dialog", "object", "embed",
]

# Hide elements whose class/id names look like overlays or site chrome.
DROP_NAME_HINTS = (
    "cookie", "consent", "gdpr", "popup", "modal", "newsletter",
    "subscription", "sidebar", "breadcrumb", "skip-link",
)

# Structural elements worth keeping, in document order.
KEEP_SELECTOR = "h1, h2, h3, p, li, dd"

_WS = re.compile(r"\s+")


def _clean_line(text: str) -> str:
    return _WS.sub(" ", text).strip()


def _drop_noise(soup: BeautifulSoup) -> None:
    for tag_name in DROP_TAGS:
        for node in soup.find_all(tag_name):
            node.decompose()
    for node in soup.find_all(True):
        hints = " ".join(node.get("class", []) + [str(node.get("id", ""))]).lower()
        if any(hint in hints for hint in DROP_NAME_HINTS):
            node.decompose()


def extract_page(html: bytes, url: str) -> tuple[str, str]:
    """Return (title, clean_text) for one page.

    Keeps the page title, meta description and the heading/paragraph/list
    text; drops scripts, navigation, footers and cookie banners. Lines that
    repeat (nav items still present) are removed.
    """
    soup = BeautifulSoup(html, "lxml")
    _drop_noise(soup)

    title = _clean_line(soup.title.get_text(" ", strip=True)) if soup.title else ""
    parts: list[str] = []

    meta = soup.find("meta", attrs={"name": "description"})
    if meta and isinstance(meta, Tag):
        content = _clean_line(str(meta.get("content", "")))
        if content:
            parts.append(content)

    seen: set[str] = set()
    for node in soup.select(KEEP_SELECTOR):
        line = _clean_line(node.get_text(" ", strip=True))
        if len(line) < 3:
            continue
        key = line.lower()
        if key in seen:
            continue
        seen.add(key)
        parts.append(line)

    text = "\n".join(parts)

    if len(text) < 80:
        # Layout-heavy page: fall back to the whole body text.
        body = soup.body or soup
        fallback = "\n".join(
            line for line in (_clean_line(x) for x in body.get_text("\n").splitlines()) if line
        )
        if len(fallback) > len(text):
            text = fallback

    return title, text
