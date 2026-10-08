"""Crawler settings. Every limit the crawler respects lives here."""

USER_AGENT = "WebIntelligenceCrawler/1.0 (+internal research)"

# How many pages we read per website (homepage + priority pages).
MAX_PAGES = 5

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

# Link discovery.
MAX_LINKS_SCANNED = 500

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
