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
from urllib.parse import urlparse, urlunparse

import httpx

from app.crawler import config
from app.crawler.browser import render_page
from app.crawler.discover import discover_pages, merge_candidates
from app.crawler.extract import extract_page
from app.crawler.fetcher import FetchError, FetchResult, RobotsRules, fetch_page, load_robots
from app.crawler.guard import GuardError
from app.crawler.quality import looks_like_shell, page_is_useful
from app.crawler.sitemap import sitemap_urls
from app.crawler.structured import structured_lines

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


def _with_structure(html: bytes, text: str) -> str:
    extra = [line for line in structured_lines(html) if line.lower() not in text.lower()]
    if not extra:
        return text
    block = "\n".join(extra)
    return f"{text}\n{block}" if text else block


async def _maybe_render(fetched: FetchResult, title: str, text: str) -> tuple[bytes, str, str, str]:
    """Use the browser only when HTTP returned a shell with too little text."""
    html = fetched.content
    if page_is_useful(text) or not looks_like_shell(text, html):
        return html, title, text, "http"
    rendered = await render_page(fetched.final_url)
    if not rendered:
        return html, title, text, "http"
    rendered_title, rendered_text = extract_page(rendered, fetched.final_url)
    if len(rendered_text) <= len(text):
        return html, title, text, "http"
    return rendered, rendered_title, rendered_text, "browser"


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

    def _absorb(label: str, url: str, title: str, text: str, method: str) -> int:
        kept = []
        for line in text.splitlines():
            key = line.lower()
            if key in seen_lines:
                continue
            seen_lines.add(key)
            kept.append(line)
        body = "\n".join(kept)
        if not body:
            return 0
        note = f"Retrieved via {method}: {url}"
        pages.append((label, url, f"{title}\n{note}\n{body}" if title else f"{note}\n{body}"))
        return len(body)

    home_title, home_text = extract_page(homepage.content, homepage.final_url)
    home_html, home_title, home_text, home_method = await _maybe_render(homepage, home_title, home_text)
    home_text = _with_structure(home_html, home_text)
    total = _absorb("homepage", homepage.final_url, home_title, home_text, home_method)

    delay = config.DOMAIN_DELAY
    strikes = 0
    # Fill the remaining page budget with the best business pages.
    if len(pages) < config.MAX_PAGES and total < config.SUFFICIENT_TEXT_CHARS:
        parsed = urlparse(homepage.final_url)
        origin = urlunparse((parsed.scheme, parsed.netloc, "", "", "", ""))
        host = (parsed.hostname or "").lower().removeprefix("www.")
        extra = await sitemap_urls(client, robots, origin, host)
        candidates = merge_candidates(discover_pages(home_html, homepage.final_url), extra)
        for link in candidates[: config.MAX_PAGES - 1]:
            if len(pages) >= config.MAX_PAGES or total >= config.SUFFICIENT_TEXT_CHARS:
                break
            if strikes >= config.BLOCK_STRIKES:
                break
            if not robots.allowed(link.url):
                continue
            try:
                result = await fetch_page(client, link.url)
            except GuardError:
                continue
            except FetchError as exc:
                if exc.kind == "blocked":
                    strikes += 1
                    delay = min(delay * 2, config.DOMAIN_DELAY_MAX)
                continue  # one bad page must not sink the site
            page_title, page_text = extract_page(result.content, result.final_url)
            page_html, page_title, page_text, method = await _maybe_render(result, page_title, page_text)
            page_text = _with_structure(page_html, page_text)
            total += _absorb(_page_label(result.final_url), result.final_url, page_title, page_text, method)
            await asyncio.sleep(delay)
            if total >= config.MAX_TEXT_CHARS:
                break

    if not pages or total < config.MIN_TEXT_CHARS:
        return CrawlResult(config.STATUS_EMPTY, None, "no usable text found", len(pages), homepage.final_url)

    sections = [f"=== PAGE: {label} | {url}\n{text}" for label, url, text in pages]
    profile = "\n\n".join(sections)
    if len(profile) > config.MAX_TEXT_CHARS:
        profile = profile[: config.MAX_TEXT_CHARS].rsplit("\n", 1)[0] + "\n...[truncated]"

    return CrawlResult(config.STATUS_CRAWLED, profile, None, len(pages), homepage.final_url)
