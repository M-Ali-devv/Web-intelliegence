"""Crawler settings. Every limit the crawler respects lives here."""

import os

USER_AGENT = "WebIntelligenceCrawler/1.0 (+internal research)"


def user_agent() -> str:
    """Use a published contact when CRAWLER_CONTACT_URL is set."""
    contact = os.environ.get("CRAWLER_CONTACT_URL", "").strip()
    if contact:
        return f"WebsiteIntelligenceBot/1.0 (+{contact})"
    return USER_AGENT

# Useful pages per website (homepage + ranked business pages).
# The prompt asks for a budget of about 5–10. Stop earlier once
# SUFFICIENT_TEXT_CHARS of cleaned text has been collected.
MAX_PAGES = 8
SUFFICIENT_TEXT_CHARS = 12_000

# Networking.
PAGE_TIMEOUT = 12.0
ROBOTS_TIMEOUT = 8.0
MAX_REDIRECTS = 5
RETRIES = 1
RETRY_BACKOFF = 1.5
MAX_BYTES_PER_PAGE = 2_000_000

# Concurrency across different websites. Inside one site we stay sequential.
CONCURRENCY = 10

# How much cleaned text one website may store (keeps the DB and later
# LLM calls manageable).
MAX_TEXT_CHARS = 60_000
MIN_TEXT_CHARS = 80

# Link and sitemap discovery. Sitemaps are candidates only; they are ranked
# with the same rules as homepage links and never crawled in full.
MAX_LINKS_SCANNED = 500
MAX_SITEMAPS = 5
MAX_SITEMAP_URLS = 100

# Politeness inside one domain. A 403/429 raises the delay and, after
# BLOCK_STRIKES, stops further pages on that site.
DOMAIN_DELAY = 0.2
DOMAIN_DELAY_MAX = 8.0
BLOCK_STRIKES = 2

# A page shorter than this, with script-shell signals, may use the browser.
MIN_USEFUL_PAGE_CHARS = 80
BROWSER_TIMEOUT = 15.0

# Status values written to Websites.status.
STATUS_PENDING = "pending"
STATUS_CRAWLING = "crawling"
STATUS_CRAWLED = "crawled"
STATUS_EMPTY = "empty"
STATUS_UNREACHABLE = "unreachable"
STATUS_BLOCKED = "blocked"
STATUS_ROBOTS_BLOCKED = "robots_blocked"
STATUS_INVALID = "invalid"
STATUS_FAILED = "failed"
