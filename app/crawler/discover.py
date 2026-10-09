"""Find the business pages worth reading (About, Services, Products...)."""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urljoin, urlparse, urlunparse

from bs4 import BeautifulSoup

from app.crawler import config

# Words that mark a high-value business page (requirements doc §6.1).
HIGH_VALUE = (
    "about", "company", "who-we-are", "whoweare", "our-story", "ourstory",
    "services", "solutions", "capabilities", "expertise", "what-we-do",
    "products", "platform", "features", "software",
)
MEDIUM_VALUE = (
    "industries", "markets", "sectors", "customers", "case-stud",
    "portfolio", "pricing", "partners",
)
LOW_VALUE = ("contact", "location", "team", "support", "faq")

# Never worth one of our limited page slots.
SKIP = (
    "blog", "guide", "news", "careers", "jobs", "legal", "privacy", "terms",
    "cookie", "login", "signin", "signup", "register", "account",
    "cart", "checkout", "search", "tag", "author", "feed", "sitemap",
    "wp-", "admin", "policy", "press", "events", "webinar", "podcast",
    ".pdf", ".jpg", ".jpeg", ".png", ".gif", ".svg", ".webp", ".zip",
    ".mp4", ".mp3", ".css", ".js", ".xml", ".rss",
)

SKIP_ANCHOR_TEXT = (
    "blog", "careers", "jobs", "privacy", "terms", "cookie", "login",
    "sign in", "sign up", "subscribe", "newsletter", "cart", "checkout",
    "download", "facebook", "twitter", "linkedin", "instagram", "youtube",
)


@dataclass
class PageLink:
    url: str
    label: str
    score: int


def _normalize(url: str) -> str:
    parsed = urlparse(url)
    path = parsed.path or "/"
    if path != "/" and path.endswith("/"):
        path = path.rstrip("/")
    host = (parsed.hostname or "").lower().removeprefix("www.")
    port = f":{parsed.port}" if parsed.port else ""
    return urlunparse((parsed.scheme.lower(), f"{host}{port}", path, "", "", ""))


def _score(path: str, anchor_text: str) -> int:
    haystack = f"{path} {anchor_text}".lower()
    if any(word in haystack for word in SKIP) or any(
        word in anchor_text.lower() for word in SKIP_ANCHOR_TEXT
    ):
        return 0
    if any(word in haystack for word in HIGH_VALUE):
        return 3
    if any(word in haystack for word in MEDIUM_VALUE):
        return 2
    if any(word in haystack for word in LOW_VALUE):
        return 1
    return 0


def merge_candidates(pages: list[PageLink], extra_urls: list[str]) -> list[PageLink]:
    """Add sitemap URLs into the ranked list. Higher score wins on a tie URL."""
    best = {item.url: item for item in pages}
    for raw in extra_urls:
        url = _normalize(raw)
        path = urlparse(url).path or "/"
        if path in ("", "/"):
            continue
        score = _score(path, "")
        if score == 0:
            continue
        existing = best.get(url)
        if existing is None or score > existing.score:
            best[url] = PageLink(url=url, label=path, score=score)
    return sorted(best.values(), key=lambda link: (-link.score, link.url))


def discover_pages(html: bytes, base_url: str) -> list[PageLink]:
    """Score same-site links by how useful they are for classification.

    Returns the best candidates first; the caller takes what fits in the
    page budget. The homepage itself is always page 1 and not listed here.
    """
    soup = BeautifulSoup(html, "lxml")
    base_host = (urlparse(base_url).hostname or "").lower().removeprefix("www.")

    best: dict[str, PageLink] = {}
    scanned = 0
    for anchor in soup.find_all("a", href=True):
        scanned += 1
        if scanned > config.MAX_LINKS_SCANNED:
            break
        href = str(anchor["href"]).strip()
        if href.startswith(("mailto:", "tel:", "javascript:", "#")):
            continue

        absolute = _normalize(urljoin(base_url, href))
        parsed = urlparse(absolute)
        host = (parsed.hostname or "").lower().removeprefix("www.")
        if host != base_host:
            continue

        anchor_text = anchor.get_text(" ", strip=True)[:120]
        score = _score(parsed.path, anchor_text)
        if score == 0:
            continue
        if parsed.path in ("", "/"):
            continue  # homepage (or duplicate of it)

        existing = best.get(absolute)
        if existing is None or score > existing.score:
            best[absolute] = PageLink(url=absolute, label=anchor_text or parsed.path, score=score)

    return sorted(best.values(), key=lambda link: (-link.score, link.url))
