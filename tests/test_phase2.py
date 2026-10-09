"""Phase 2: sitemap ranking, content quality, structured data, browser fallback."""

import asyncio

import pytest

from app.crawler import config
from app.crawler.pipeline import crawl_one
from app.crawler.quality import looks_like_shell, page_is_useful
from app.crawler.sitemap import parse_sitemap
from app.crawler.structured import structured_lines
from tests.helpers import html_client


def run_crawl(client, url="https://example.com/"):
    return asyncio.run(crawl_one(client, url, {}))


SITEMAP = """<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>https://example.com/locations</loc></url>
  <url><loc>https://example.com/blog/news</loc></url>
  <url><loc>https://other.test/about</loc></url>
</urlset>
"""

HOME = """
<html><head><title>ACME</title></head><body>
<a href="/about">About</a>
<h1>ACME Robotics</h1>
<p>ACME builds picking and sorting robots for warehouses around the world,
with deployments in more than forty countries and support hubs on four continents.</p>
</body></html>
"""

ABOUT = """
<html><head><title>About</title></head><body>
<h1>About us</h1>
<p>Founded in 1998, ACME employs nine hundred engineers and factory specialists.</p>
</body></html>
"""

LOCATIONS = """
<html><head><title>Locations</title></head><body>
<h1>Locations</h1>
<p>Service centers in Hamburg, Singapore, and Houston support every installation.</p>
</body></html>
"""


def test_parse_sitemap_index_and_urlset():
    index = b"""<sitemapindex><sitemap><loc>https://example.com/a.xml</loc></sitemap></sitemapindex>"""
    kind, locs = parse_sitemap(index)
    assert kind == "index"
    assert locs == ["https://example.com/a.xml"]
    kind, locs = parse_sitemap(SITEMAP.encode())
    assert kind == "urlset"
    assert "https://example.com/locations" in locs
    assert parse_sitemap(b"not xml") == ("invalid", [])


def test_sitemap_candidate_is_crawled_and_junk_is_not(allow_hosts):
    robots = "User-agent: *\nSitemap: https://example.com/sitemap.xml\n"
    pages = {
        "/": HOME,
        "/about": ABOUT,
        "/sitemap.xml": SITEMAP,
        "/locations": LOCATIONS,
        "/blog/news": "<html><body><p>A blog post that should not be fetched.</p></body></html>",
    }
    result = run_crawl(html_client(pages, robots=robots))
    assert result.status == "crawled"
    assert "=== PAGE: locations | https://example.com/locations" in result.clean_text
    assert "Hamburg" in result.clean_text
    assert "blog post" not in result.clean_text
    assert "Retrieved via http: https://example.com/" in result.clean_text


def test_shell_page_uses_browser_only_when_http_is_thin(allow_hosts, monkeypatch):
    calls = []

    async def fake_render(url):
        calls.append(url)
        return (
            "<html><head><title>Rendered ACME</title></head><body>"
            "<p>We manufacture industrial valves and control systems for chemical plants across three regions worldwide.</p>"
            "</body></html>"
        ).encode()

    monkeypatch.setattr("app.crawler.pipeline.render_page", fake_render)
    shell = "<html><head><title>App</title></head><body><div id='root'></div><script src='/app.js'></script></body></html>"
    result = run_crawl(html_client({"/": shell}))
    assert calls == ["https://example.com/"]
    assert result.status == "crawled"
    assert "Retrieved via browser: https://example.com/" in result.clean_text
    assert "industrial valves" in result.clean_text


def test_useful_http_page_does_not_open_a_browser(allow_hosts, monkeypatch):
    async def fail_render(url):
        raise AssertionError(url)

    monkeypatch.setattr("app.crawler.pipeline.render_page", fail_render)
    result = run_crawl(html_client({"/": HOME, "/about": ABOUT}))
    assert result.status == "crawled"
    assert "Retrieved via http" in result.clean_text


def test_repeated_blocks_stop_the_rest_of_the_site(allow_hosts, monkeypatch):
    monkeypatch.setattr(config, "BLOCK_STRIKES", 1)
    pages = {"/": HOME, "/about": ABOUT}
    client = html_client(pages)

    def handler(request):
        if request.url.path == "/about":
            return __import__("httpx").Response(429, text="slow down", request=request)
        return client._transport.handle_request  # not used

    # Serve about as 429 through a custom client built on html_client's pages.
    import httpx

    def route(request):
        path = request.url.path
        if path == "/robots.txt":
            return httpx.Response(404, request=request)
        if path == "/about":
            return httpx.Response(429, text="slow down", request=request)
        if path in pages:
            return httpx.Response(200, html=pages[path], request=request)
        return httpx.Response(404, text="no", request=request)

    from tests.helpers import make_client

    result = run_crawl(make_client(route))
    assert result.status == "crawled"
    assert "=== PAGE: about" not in result.clean_text
    assert result.pages_crawled == 1


def test_quality_flags_script_shell_and_accepts_real_copy():
    shell = b"<html><body><script>app()</script><noscript>enable javascript</noscript></body></html>"
    assert looks_like_shell("", shell) is True
    prose = "ACME builds warehouse robots for factories across forty countries and runs support hubs."
    assert page_is_useful(prose) is True
    assert looks_like_shell(prose, b"<html><script></script><p>" + prose.encode() + b"</p></html>") is False


def test_structured_data_lines_keep_source_fields():
    html = b"""
    <html><head>
      <meta property="og:description" content="Robots for warehouses">
      <link rel="canonical" href="https://example.com/">
      <script type="application/ld+json">
        {"@type": "Organization", "name": "ACME Robotics", "description": "Warehouse automation"}
      </script>
    </head><body></body></html>
    """
    lines = structured_lines(html)
    assert any("ACME Robotics" in line and "Organization" in line for line in lines)
    assert any(line.startswith("Open Graph og:description:") for line in lines)
    assert "Canonical: https://example.com/" in lines


def test_stops_early_when_enough_text_is_collected(allow_hosts, monkeypatch):
    monkeypatch.setattr(config, "SUFFICIENT_TEXT_CHARS", 80)
    result = run_crawl(html_client({"/": HOME, "/about": ABOUT, "/locations": LOCATIONS}))
    assert result.pages_crawled == 1
    assert "=== PAGE: about" not in result.clean_text
