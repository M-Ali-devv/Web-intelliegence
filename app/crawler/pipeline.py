"""Crawl one website: homepage first, then the best business pages.

Result contract for the rest of the team:
    status      one of crawler.config status values
    clean_text  consolidated pages (or None when nothing usable was found)
    error       short failure reason for Websites.crawl_error
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from urllib.parse import urlparse

import httpx

from app.crawler import config
from app.crawler.discover import discover_pages
from app.crawler.extract import extract_page
from app.crawler.fetcher import FetchError, RobotsRules, fetch_page, load_robots
from app.crawler.guard import GuardError

_STATUS_BY_ERROR = {
    "network": config.STATUS_UNREACHABLE,
    "blocked": config.STATUS_BLOCKED,
    "http": config.STATUS_FAILED,
    "redirect": config.STATUS_FAILED,
    "too_large": config.STATUS_FAILED,
    "not_html": config.STATUS_FAILED,
}

_WS = re.compile(r"\s+")


@dataclass
class CrawlResult:
    status: str
    clean_text: str | None
    error: str | None
    pages_crawled: int
    final_url: str | None = None


def _page_label(url: str) -> str:
    path = (urlparse(url).path or "/").rstrip("/")
    if not path:
        return "homepage"
    segment = path.rsplit("/", 1)[-1]
    return re.sub(r"[-_]+", " ", segment)[:40] or "homepage"


def _homepage_candidates(normalized_url: str) -> list[str]:
    """https first (the stored form); plain http as a fallback for old sites."""
    candidates = [normalized_url]
    if normalized_url.startswith("https://"):
        candidates.append("http://" + normalized_url[len("https://"):])
    return candidates


async def _fetch_homepage(client: httpx.AsyncClient, robots: RobotsRules, normalized_url: str):
    last: FetchError | None = None
    for candidate in _homepage_candidates(normalized_url):
        if not robots.allowed(candidate):
            raise FetchError("robots", "disallowed by robots.txt")
        try:
            return await fetch_page(client, candidate)
        except FetchError as exc:
            if exc.kind == "network":
                last = exc
                continue
            raise
    raise last if last else FetchError("network", normalized_url)


async def crawl_one(
    client: httpx.AsyncClient,
    normalized_url: str,
    robots_cache: dict[str, RobotsRules],
) -> CrawlResult:
    """Fetch and clean a single website. Never raises for normal failures."""
    try:
        robots = await load_robots(client, normalized_url, robots_cache)
        homepage = await _fetch_homepage(client, robots, normalized_url)
    except GuardError as exc:
        return CrawlResult(config.STATUS_INVALID, None, str(exc), 0)
    except FetchError as exc:
        status = (
            config.STATUS_ROBOTS_BLOCKED if exc.kind == "robots"
            else _STATUS_BY_ERROR.get(exc.kind, config.STATUS_FAILED)
        )
        return CrawlResult(status, None, exc.detail, 0)

    pages: list[tuple[str, str, str]] = []  # (label, final_url, text)
    seen_lines: set[str] = set()

    def _absorb(label: str, url: str, title: str, text: str) -> int:
        kept = []
        for line in text.splitlines():
            key = line.lower()
            if key in seen_lines:
                continue
            seen_lines.add(key)
            kept.append(line)
        body = "\n".join(kept)
        if body:
            pages.append((label, url, (f"{title}\n{body}" if title else body)))
        return len(body)

    title, home_text = extract_page(homepage.content, homepage.final_url)
    total = _absorb("homepage", homepage.final_url, title, home_text)

    # Fill the remaining page budget with the best business pages.
    if len(pages) < config.MAX_PAGES:
        candidates = discover_pages(homepage.content, homepage.final_url)
        for link in candidates[: config.MAX_PAGES - 1]:
            if not robots.allowed(link.url):
                continue
            try:
                result = await fetch_page(client, link.url)
            except (FetchError, GuardError):
                continue  # one bad page must not sink the site
            page_title, page_text = extract_page(result.content, result.final_url)
            total += _absorb(_page_label(result.final_url), result.final_url, page_title, page_text)
            await asyncio.sleep(0.2)  # stay polite inside a single domain
            if total >= config.MAX_TEXT_CHARS:
                break

    if not pages or total < config.MIN_TEXT_CHARS:
        return CrawlResult(config.STATUS_EMPTY, None, "no usable text found", len(pages), homepage.final_url)

    sections = [f"=== PAGE: {label} | {url}\n{text}" for label, url, text in pages]
    profile = "\n\n".join(sections)
    if len(profile) > config.MAX_TEXT_CHARS:
        profile = profile[: config.MAX_TEXT_CHARS].rsplit("\n", 1)[0] + "\n...[truncated]"

    return CrawlResult(config.STATUS_CRAWLED, profile, None, len(pages), homepage.final_url)
