"""Priority-page discovery: About/Services first, junk never."""

from app.crawler import config
from app.crawler.discover import discover_pages


def links(html: str) -> list[str]:
    return [link.url for link in discover_pages(html.encode(), "https://example.com")]


def test_priority_pages_scored_and_sorted():
    html = """
    <html><body>
      <a href="/contact">Contact</a>
      <a href="/about">About us</a>
      <a href="/services">Our services</a>
      <a href="/random">Random</a>
    </body></html>
    """
    found = links(html)
    assert "https://example.com/about" in found
    assert "https://example.com/services" in found
    assert "https://example.com/contact" in found
    assert "https://example.com/random" not in found  # no score
    assert found.index("https://example.com/about") < found.index("https://example.com/contact")


def test_industries_and_products_scored():
    html = """
    <html><body>
      <a href="/industries/healthcare">Industries</a>
      <a href="/products/robot-arm">Products</a>
      <a href="/case-studies/acme">Case studies</a>
    </body></html>
    """
    found = links(html)
    assert len(found) == 3


def test_excludes_junk_paths():
    html = """
    <html><body>
      <a href="/blog/post-1">Blog</a>
      <a href="/guides/setup">Guide</a>
      <a href="/careers">Jobs</a>
      <a href="/privacy-policy">Privacy</a>
      <a href="/brochure.pdf">PDF</a>
      <a href="/logo.png">Image</a>
      <a href="/wp-admin/">Admin</a>
      <a href="/tag/startup">Tag</a>
    </body></html>
    """
    assert links(html) == []


def test_excludes_offsite_and_subdomain_links():
    html = """
    <html><body>
      <a href="https://evil.com/about">Evil</a>
      <a href="https://shop.example.com/products">Shop subdomain</a>
      <a href="/about">Real about</a>
    </body></html>
    """
    found = links(html)
    assert found == ["https://example.com/about"]


def test_www_variant_matches_base_host():
    html = '<html><body><a href="https://www.example.com/about">About</a></body></html>'
    assert links(html) == ["https://example.com/about"]


def test_skips_schemes_and_fragments():
    html = """
    <html><body>
      <a href="mailto:hi@example.com">Mail</a>
      <a href="tel:+123456">Call</a>
      <a href="javascript:void(0)">JS</a>
      <a href="#top">Anchor</a>
      <a href="/services">Services</a>
    </body></html>
    """
    assert links(html) == ["https://example.com/services"]


def test_homepage_links_excluded():
    html = """
    <html><body>
      <a href="/">Home</a>
      <a href="https://example.com">Home2</a>
      <a href="/about">About</a>
    </body></html>
    """
    assert links(html) == ["https://example.com/about"]


def test_dedupes_slash_and_fragment_variants():
    html = """
    <html><body>
      <a href="/about">About</a>
      <a href="/about/">About slash</a>
      <a href="/about#team">About frag</a>
    </body></html>
    """
    assert links(html) == ["https://example.com/about"]


def test_anchor_text_careers_skipped_even_on_clean_path():
    html = '<html><body><a href="/join">Careers at ACME</a><a href="/about">About</a></body></html>'
    assert links(html) == ["https://example.com/about"]


def test_max_links_scanned_limit(monkeypatch):
    monkeypatch.setattr(config, "MAX_LINKS_SCANNED", 2)
    html = """
    <html><body>
      <a href="/about">About</a>
      <a href="/services">Services</a>
      <a href="/products">Products</a>
    </body></html>
    """
    found = links(html)
    assert "https://example.com/products" not in found


def test_query_strings_dropped_from_identity():
    html = """
    <html><body>
      <a href="/about?ref=nav">A</a>
      <a href="/about?ref=footer">B</a>
    </body></html>
    """
    assert links(html) == ["https://example.com/about"]


def test_empty_html_returns_empty_list():
    assert discover_pages(b"", "https://example.com") == []
    assert discover_pages(b"<html><body><p>no links</p></body></html>", "https://example.com") == []


def test_homepage_link_excluded_even_when_it_scores():
    html = '<html><body><a href="/">About</a><a href="/services">Services</a></body></html>'
    assert links(html) == ["https://example.com/services"]
