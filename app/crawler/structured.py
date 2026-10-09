"""Pull machine-readable hints out of a page. These support the text; they are not trusted blindly."""

from __future__ import annotations

import json

from bs4 import BeautifulSoup, Tag

_INTERESTING = {
    "organization",
    "localbusiness",
    "corporation",
    "store",
    "product",
    "service",
    "offer",
}
_MAX_LINES = 12


def _clean(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return " ".join(value.split())
    if isinstance(value, dict):
        parts = [_clean(value.get(key)) for key in ("streetAddress", "addressLocality", "addressRegion", "addressCountry")]
        return ", ".join(part for part in parts if part)
    if isinstance(value, list):
        return ", ".join(_clean(item) for item in value if _clean(item))
    return ""


def _types(node: dict) -> list[str]:
    raw = node.get("@type", "")
    if isinstance(raw, list):
        return [str(item) for item in raw]
    if raw:
        return [str(raw)]
    return []


def _from_jsonld(html: bytes) -> list[str]:
    soup = BeautifulSoup(html, "lxml")
    lines: list[str] = []
    for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
        raw = script.string or script.get_text() or ""
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            continue
        nodes = payload if isinstance(payload, list) else [payload]
        expanded: list[dict] = []
        for node in nodes:
            if not isinstance(node, dict):
                continue
            graph = node.get("@graph")
            if isinstance(graph, list):
                expanded.extend(item for item in graph if isinstance(item, dict))
            else:
                expanded.append(node)
        for node in expanded:
            types = _types(node)
            if not any(item.lower() in _INTERESTING for item in types):
                continue
            label = types[0]
            for field in ("name", "description", "address"):
                value = _clean(node.get(field))
                if value:
                    lines.append(f"Structured data ({label}): {field}: {value[:300]}")
                if len(lines) >= _MAX_LINES:
                    return lines
    return lines


def structured_lines(html: bytes) -> list[str]:
    """Short evidence lines from JSON-LD, Open Graph, and the canonical URL."""
    lines = _from_jsonld(html)
    soup = BeautifulSoup(html, "lxml")
    for prop in ("og:title", "og:description"):
        tag = soup.find("meta", attrs={"property": prop})
        if isinstance(tag, Tag):
            value = _clean(tag.get("content"))
            if value:
                lines.append(f"Open Graph {prop}: {value[:300]}")
    canonical = soup.find("link", attrs={"rel": "canonical"})
    if isinstance(canonical, Tag) and canonical.get("href"):
        lines.append(f"Canonical: {_clean(canonical.get('href'))[:300]}")
    return lines[:_MAX_LINES]
