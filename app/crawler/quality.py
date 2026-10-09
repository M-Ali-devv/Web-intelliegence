"""Decide whether an HTTP response is useful business content."""

from __future__ import annotations

from app.crawler import config

# Phrases that show up on empty application shells, not on real copy.
_SHELL_HINTS = (
    "enable javascript",
    "you need to enable javascript",
    "please enable javascript",
    "just a moment",
    "checking your browser",
)


def looks_like_shell(text: str, html: bytes) -> bool:
    """True when the HTML likely needs a browser to reveal the page."""
    if len(text) >= config.MIN_USEFUL_PAGE_CHARS:
        return False
    raw = html.decode("utf-8", errors="ignore").lower()
    if any(hint in raw for hint in _SHELL_HINTS):
        return True
    return b"<script" in html.lower()


def page_is_useful(text: str) -> bool:
    return len(text) >= config.MIN_USEFUL_PAGE_CHARS
