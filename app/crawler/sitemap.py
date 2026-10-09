"""Find candidate pages from robots.txt and sitemap files.

The sitemap is not crawled page by page. It only supplies URLs, which the
focused ranker then accepts or rejects.
"""

from __future__ import annotations

import gzip
from urllib.parse import urlparse
from xml.etree import ElementTree

import httpx

from app.crawler import config
from app.crawler.fetcher import FetchError, RobotsRules, fetch_page
from app.crawler.guard import GuardError


def _same_site(url: str, base_host: str) -> bool:
    host = (urlparse(url).hostname or "").lower().removeprefix("www.")
    return host == base_host


def parse_sitemap(content: bytes) -> tuple[str, list[str]]:
    """Return ('index'|'urlset'|'invalid', locations)."""
    if content[:2] == b"\x1f\x8b":
        try:
            content = gzip.decompress(content)
        except OSError:
            return "invalid", []
    try:
        root = ElementTree.fromstring(content)
    except ElementTree.ParseError:
        return "invalid", []

    tag = root.tag.rsplit("}", 1)[-1].lower()
    locs = []
    for node in root.iter():
        if node.tag.rsplit("}", 1)[-1].lower() == "loc" and node.text:
            locs.append(node.text.strip())
    if tag == "sitemapindex":
        return "index", locs
    if tag == "urlset":
        return "urlset", locs
    return "invalid", []


async def sitemap_urls(
    client: httpx.AsyncClient,
    robots: RobotsRules,
    origin: str,
    base_host: str,
) -> list[str]:
    """Collect same-site page URLs from a bounded number of sitemap files."""
    seeds = list(robots.parser.site_maps() or [])
    if not seeds:
        seeds = [f"{origin}/sitemap.xml"]

    queue = seeds[: config.MAX_SITEMAPS]
    seen_files: set[str] = set()
    found: list[str] = []
    seen_pages: set[str] = set()

    while queue and len(seen_files) < config.MAX_SITEMAPS and len(found) < config.MAX_SITEMAP_URLS:
        sitemap = queue.pop(0)
        if sitemap in seen_files or not robots.allowed(sitemap):
            continue
        seen_files.add(sitemap)
        try:
            result = await fetch_page(client, sitemap, expect_html=False)
        except (FetchError, GuardError):
            continue
        kind, locs = parse_sitemap(result.content)
        if kind == "index":
            for loc in locs:
                if loc not in seen_files:
                    queue.append(loc)
            continue
        if kind != "urlset":
            continue
        for loc in locs:
            if len(found) >= config.MAX_SITEMAP_URLS:
                break
            if not _same_site(loc, base_host) or loc in seen_pages:
                continue
            if not robots.allowed(loc):
                continue
            seen_pages.add(loc)
            found.append(loc)
    return found
