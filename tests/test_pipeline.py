"""crawl_one: status mapping, page budget, profile format, failure paths."""

import asyncio

import httpx
import pytest

from app.crawler import config
from app.crawler.guard import GuardError
from app.crawler.pipeline import crawl_one
from tests.helpers import html_client, make_client

HOME = """
<html><head><title>ACME — Home</title></head><body>
<nav><a href="/about">About</a> <a href="/services">Services</a> <a href="/blog/x">Blog</a></nav>
<h1>ACME Robotics</h1>
<p>ACME builds picking and sorting robots for warehouses around the world,
with deployments in more than forty countries and support hubs on four continents.</p>
<p>Trusted by 10,000 companies worldwide every single day of the year.</p>
</body></html>
"""

ABOUT = """
<html><head><title>About ACME</title></head><body>
<h1>About us</h1>
<p>Founded in 1998, ACME employs nine hundred engineers and factory specialists.</p>
<p>Trusted by 10,000 companies worldwide every single day of the year.</p>
</body></html>
"""

SERVICES = """
<html><head><title>Services</title></head><body>
<h1>Our services</h1>
<p>We offer installation, maintenance, remote monitoring and custom integration services.</p>
</body></html>
"""

FULL_SITE = {"/": HOME, "/about": ABOUT, "/services": SERVICES}


def run_crawl(client, url="https://example.com/"):
    return asyncio.run(crawl_one(client, url, {}))


def test_happy_path_crawls_homepage_and_priority_pages(allow_hosts):
    result = run_crawl(html_client(FULL_SITE))
    assert result.status == "crawled"
    assert result.pages_crawled == 3
    assert result.error is None
    assert result.clean_text.startswith("=== PAGE: homepage | https://example.com/")
    assert "=== PAGE: about | https://example.com/about" in result.clean_text
    assert "=== PAGE: services | https://example.com/services" in result.clean_text
    assert "Founded in 1998" in result.clean_text
    assert "installation, maintenance" in result.clean_text


def test_boilerplate_repeated_across_pages_kept_once(allow_hosts):
    result = run_crawl(html_client(FULL_SITE))
    assert result.clean_text.count("Trusted by 10,000 companies") == 1


def test_page_budget_respected(allow_hosts, monkeypatch):
    many_links = {"/": HOME, "/about": ABOUT, "/services": SERVICES}
    html = HOME.replace("/blog/x", "/about")
    monkeypatch.setattr(config, "MAX_PAGES", 2)
    result = run_crawl(html_client({"/": html, "/about": ABOUT, "/services": SERVICES}))
    assert result.pages_crawled <= 2
    assert many_links  # sanity


def test_profile_truncated_at_max_chars(allow_hosts, monkeypatch):
    monkeypatch.setattr(config, "MAX_TEXT_CHARS", 400)
    result = run_crawl(html_client(FULL_SITE))
    assert result.status == "crawled"
    assert result.clean_text.endswith("...[truncated]")
    assert len(result.clean_text) <= 400 + len("\n...[truncated]")


def test_empty_when_below_minimum_text(allow_hosts, monkeypatch):
    monkeypatch.setattr(config, "MIN_TEXT_CHARS", 10_000)
    result = run_crawl(html_client({"/": HOME}))
    assert result.status == "empty"
    assert result.clean_text is None
    assert "no usable text" in result.error


def test_unreachable_on_connection_failure(allow_hosts):
    def handler(request):
        raise httpx.ConnectError("refused", request=request)

    result = run_crawl(make_client(handler))
    assert result.status == "unreachable"
    assert result.clean_text is None
    assert "connection error" in result.error


def test_blocked_on_403(allow_hosts):
    result = run_crawl(html_client({}, default_status=403))
    assert result.status == "blocked"
    assert "HTTP 403" in result.error


def test_failed_on_homepage_404(allow_hosts):
    result = run_crawl(html_client({}))
    assert result.status == "failed"
    assert "HTTP 404" in result.error


def test_failed_when_homepage_is_not_html(allow_hosts):
    def handler(request):
        return httpx.Response(
            200, content=b"%PDF-1.4", headers={"content-type": "application/pdf"}, request=request
        )

    result = run_crawl(make_client(handler))
    assert result.status == "failed"
    assert "content-type" in result.error


def test_robots_blocked_when_disallowed(allow_hosts):
    robots = "User-agent: *\nDisallow: /"
    result = run_crawl(html_client({"/": HOME}, robots=robots))
    assert result.status == "robots_blocked"
    assert result.clean_text is None


def test_robots_blocks_only_disallowed_pages(allow_hosts):
    robots = "User-agent: *\nDisallow: /services"
    result = run_crawl(html_client(FULL_SITE, robots=robots))
    assert result.status == "crawled"
    assert "=== PAGE: services" not in result.clean_text
    assert "=== PAGE: about" in result.clean_text
    assert result.pages_crawled == 2


def test_invalid_when_guard_rejects(monkeypatch):
    def reject(url):
        raise GuardError("host points at non-public address")

    monkeypatch.setattr("app.crawler.fetcher.assert_public_url", reject)
    result = run_crawl(html_client({"/": HOME}))
    assert result.status == "invalid"
    assert "non-public" in result.error


def test_broken_candidate_page_skipped(allow_hosts):
    pages = {"/": HOME, "/about": ABOUT}  # /services link exists but 404s
    result = run_crawl(html_client(pages))
    assert result.status == "crawled"
    assert result.pages_crawled == 2
    assert "=== PAGE: about" in result.clean_text


def test_candidate_with_no_text_skipped(allow_hosts):
    script_only = "<html><head><title>x</title></head><body><script>var a=1;</script></body></html>"
    pages = {"/": HOME, "/about": script_only, "/services": SERVICES}
    result = run_crawl(html_client(pages))
    assert result.status == "crawled"
    assert result.pages_crawled == 2  # homepage + services; empty about skipped


def test_falls_back_to_http_when_https_fails(allow_hosts):
    def handler(request):
        if request.url.scheme == "https":
            raise httpx.ConnectError("no tls", request=request)
        if request.url.path == "/robots.txt":
            return httpx.Response(404, request=request)
        return httpx.Response(200, html=HOME, request=request)

    result = run_crawl(make_client(handler))
    assert result.status == "crawled"
    assert result.final_url.startswith("http://")


def test_single_page_site_still_crawled(allow_hosts):
    solo = """
    <html><head><title>Solo</title></head><body>
    <p>A single-page company site describing our consultancy offering
    for small businesses in the regional market, with no other links at all.</p>
    </body></html>
    """
    result = run_crawl(html_client({"/": solo}))
    assert result.status == "crawled"
    assert result.pages_crawled == 1
    assert result.clean_text.count("=== PAGE:") == 1


def test_page_label_formats():
    from app.crawler.pipeline import _page_label

    assert _page_label("https://example.com/") == "homepage"
    assert _page_label("https://example.com/about-us") == "about us"
    assert _page_label("https://example.com/our_story") == "our story"
    assert len(_page_label("https://example.com/" + "x" * 80)) == 40
