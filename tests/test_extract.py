"""HTML → clean text: keeps business content, drops boilerplate."""

from app.crawler.extract import extract_page

BASE = """
<html><head><title>ACME Corp — Home</title>
<meta name="description" content="ACME builds industrial robots.">
<style>.x{color:red}</style></head>
<body>
<nav><a href="/">Home</a> <a href="/about">About</a></nav>
<header>ACME Corp</header>
<h1>Industrial Robotics</h1>
<p>ACME designs and manufactures warehouse robots for logistics companies.</p>
<ul><li>Picking arms</li><li>Sorting systems</li></ul>
<footer>© ACME Inc — Privacy Policy — Terms</footer>
</body></html>
"""


def test_keeps_title_headings_paragraphs_lists():
    title, text = extract_page(BASE.encode(), "https://acme.test")
    assert title == "ACME Corp — Home"
    assert "Industrial Robotics" in text
    assert "warehouse robots" in text
    assert "Picking arms" in text


def test_removes_nav_header_footer_script_style():
    _, text = extract_page(BASE.encode(), "https://acme.test")
    assert "Privacy Policy" not in text  # footer gone
    assert "color:red" not in text  # style gone
    assert "<a href" not in text


def test_includes_meta_description():
    _, text = extract_page(BASE.encode(), "https://acme.test")
    assert "ACME builds industrial robots." in text


def test_removes_cookie_banner_by_class():
    html = b"""
    <html><head><title>S</title></head><body>
    <div class="cookie-consent-banner">We use cookies</div>
    <h1>Real heading</h1><p>Enough real text to pass the minimum length check
    for this page so the extractor keeps the main content around.</p>
    </body></html>
    """
    _, text = extract_page(html, "https://s.test")
    assert "We use cookies" not in text
    assert "Real heading" in text


def test_dedupes_repeated_lines():
    html = b"""
    <html><head><title>S</title></head><body>
    <p>Sign up for our newsletter today and never miss an update from us.</p>
    <p>Sign up for our newsletter today and never miss an update from us.</p>
    <p>Unique content line number one about our services and products.</p>
    </body></html>
    """
    _, text = extract_page(html, "https://s.test")
    assert text.count("Sign up for our newsletter") == 1
    assert "Unique content line number one" in text


def test_empty_input_returns_empty_strings():
    title, text = extract_page(b"", "https://s.test")
    assert title == ""
    assert text == ""


def test_malformed_html_does_not_crash():
    garbage = b"<html><body><p>Broken<div><span>structure</p></body><table><tr><td>cell"
    _, text = extract_page(garbage, "https://s.test")
    assert "Broken" in text or "structure" in text


def test_non_utf8_bytes_do_not_crash():
    raw = "<html><head><title>Café</title></head><body><p>Qualité</p></body></html>".encode("latin-1")
    title, text = extract_page(raw, "https://s.test")
    assert "Caf" in title  # decoded without raising


def test_collapses_whitespace():
    html = b"<html><head><title>T</title></head><body><p>too    many\n\nspaces\t here</p></body></html>"
    _, text = extract_page(html, "https://s.test")
    assert "  " not in text


def test_layout_page_falls_back_to_body_text():
    html = b"""
    <html><head><title>Layout</title></head><body>
    <div><div><div>Deeply nested but meaningful company description text
    that lives inside divs instead of paragraph tags entirely.</div></div></div>
    </body></html>
    """
    _, text = extract_page(html, "https://s.test")
    assert "Deeply nested" in text


def test_unicode_content_preserved():
    html = "<html><head><title>شرکت</title></head><body><p>ما راه‌حل‌های نرم‌افزاری ارائه می‌دهیم</p></body></html>".encode()
    title, text = extract_page(html, "https://s.test")
    assert "شرکت" in title
    assert "نرم‌افزاری" in text


def test_tiny_lines_dropped():
    html = b"""
    <html><head><title>T</title></head><body>
    <p>OK</p>
    <p>We provide comprehensive logistics software solutions for enterprises
    across many regions with long term support contracts and training.</p>
    </body></html>
    """
    _, text = extract_page(html, "https://s.test")
    assert "OK" not in text.split()
    assert "logistics software" in text
