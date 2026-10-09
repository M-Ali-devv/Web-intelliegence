"""HTTP fetching: guarded redirects, retries, size limits, robots.txt."""

from __future__ import annotations

from dataclasses import dataclass, field
from urllib import robotparser
from urllib.parse import urljoin, urlparse, urlunparse

import time

import httpx

from app.crawler import config
from app.crawler.guard import GuardError, assert_public_url


class FetchError(Exception):
    """A page could not be fetched. `.kind` decides the row status."""

    def __init__(self, kind: str, detail: str):
        super().__init__(detail)
        self.kind = kind
        self.detail = detail


@dataclass
class FetchResult:
    requested_url: str
    final_url: str
    status_code: int
    content: bytes
    content_type: str
    elapsed_ms: int = 0


def _request_headers() -> dict[str, str]:
    return {
        "User-Agent": config.user_agent(),
        "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.5",
        "Accept-Language": "en, *;q=0.5",
    }


async def _one_attempt(client: httpx.AsyncClient, url: str, expect_html: bool) -> FetchResult:
    started = time.perf_counter()
    current = url
    for _ in range(config.MAX_REDIRECTS + 1):
        assert_public_url(current)
        try:
            response = await client.get(
                current,
                headers=_request_headers(),
                follow_redirects=False,
                timeout=config.PAGE_TIMEOUT,
            )
        except httpx.TimeoutException as exc:
            raise FetchError("network", f"timeout fetching {current}") from exc
        except httpx.HTTPError as exc:
            raise FetchError("network", f"connection error fetching {current}: {exc}") from exc

        if response.status_code in (301, 302, 303, 307, 308):
            location = response.headers.get("location")
            if not location:
                raise FetchError("redirect", f"redirect without Location from {current}")
            current = urljoin(current, location)
            continue

        if response.status_code in (401, 403, 429):
            raise FetchError("blocked", f"HTTP {response.status_code} from {current}")
        if not 200 <= response.status_code < 300:
            raise FetchError("http", f"HTTP {response.status_code} from {current}")

        content_type = response.headers.get("content-type", "").lower()
        if expect_html and "html" not in content_type and content_type:
            raise FetchError("not_html", f"content-type {content_type!r} from {current}")

        declared = response.headers.get("content-length")
        if declared and declared.isdigit() and int(declared) > config.MAX_BYTES_PER_PAGE:
            raise FetchError("too_large", f"page larger than {config.MAX_BYTES_PER_PAGE} bytes")
        if len(response.content) > config.MAX_BYTES_PER_PAGE:
            raise FetchError("too_large", f"page larger than {config.MAX_BYTES_PER_PAGE} bytes")

        return FetchResult(
            elapsed_ms=int((time.perf_counter() - started) * 1000),
            requested_url=url,
            final_url=str(response.url),
            status_code=response.status_code,
            content=response.content,
            content_type=content_type,
        )

    raise FetchError("redirect", f"more than {config.MAX_REDIRECTS} redirects from {url}")


async def fetch_page(client: httpx.AsyncClient, url: str, expect_html: bool = True) -> FetchResult:
    """Fetch one URL. Retries transient network failures and 5xx once."""
    last_error: FetchError | None = None
    for attempt in range(config.RETRIES + 1):
        if attempt:
            import asyncio

            await asyncio.sleep(config.RETRY_BACKOFF * attempt)
        try:
            return await _one_attempt(client, url, expect_html)
        except FetchError as exc:
            if exc.kind == "network" or (exc.kind == "http" and "HTTP 5" in exc.detail):
                last_error = exc
                continue
            raise
    raise last_error if last_error else FetchError("network", f"failed to fetch {url}")


@dataclass
class RobotsRules:
    origin: str
    parser: robotparser.RobotFileParser
    missing: bool = False

    def allowed(self, url: str) -> bool:
        return self.missing or self.parser.can_fetch(config.user_agent(), url)


async def load_robots(client: httpx.AsyncClient, url: str, cache: dict[str, RobotsRules]) -> RobotsRules:
    """Fetch robots.txt for the URL's origin. Unavailable robots = allow."""
    parsed = urlparse(url)
    origin = urlunparse((parsed.scheme, parsed.netloc, "", "", "", ""))
    if origin in cache:
        return cache[origin]

    rules = RobotsRules(origin=origin, parser=robotparser.RobotFileParser())
    try:
        result = await fetch_page(client, f"{origin}/robots.txt", expect_html=False)
        rules.parser.parse(result.content.decode("utf-8", errors="replace").splitlines())
    except FetchError as exc:
        if exc.kind == "http" and "HTTP 404" in exc.detail:
            rules.missing = True
        # Network trouble or 5xx: fail open, same as a missing robots.txt.
        else:
            rules.missing = True
    except GuardError:
        raise
    cache[origin] = rules
    return rules
