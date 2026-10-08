"""HTTP layer: redirects, retries, robots.txt, size/type limits — all mocked."""

import asyncio

import httpx
import pytest

from app.crawler import config
from app.crawler.fetcher import FetchError, load_robots
from app.crawler.guard import GuardError
from tests.helpers import make_client


def fetch(client, url, expect_html=True):
    return asyncio.run(fetch_page_async(client, url, expect_html))


async def fetch_page_async(client, url, expect_html=True):
    from app.crawler.fetcher import fetch_page

    return await fetch_page(client, url, expect_html)


@pytest.fixture(autouse=True)
def fast_retries(monkeypatch):
    monkeypatch.setattr(config, "RETRY_BACKOFF", 0)


# --- happy path -----------------------------------------------------------


def test_fetch_200_returns_content_and_final_url():
    def handler(request):
        return httpx.Response(200, html="<html>hi</html>", request=request)

    result = fetch(make_client(handler), "https://example.com/")
    assert result.status_code == 200
    assert result.content == b"<html>hi</html>"
    assert result.final_url == "https://example.com/"


# --- redirects ------------------------------------------------------------


def test_redirect_chain_followed():
    def handler(request):
        path = request.url.path
        if path == "/start":
            return httpx.Response(301, headers={"location": "/mid"}, request=request)
        if path == "/mid":
            return httpx.Response(302, headers={"location": "https://example.com/end"}, request=request)
        return httpx.Response(200, html="<html>done</html>", request=request)

    result = fetch(make_client(handler), "https://example.com/start")
    assert result.final_url == "https://example.com/end"
    assert result.content == b"<html>done</html>"


def test_redirect_without_location_is_error():
    def handler(request):
        return httpx.Response(301, request=request)

    with pytest.raises(FetchError) as exc:
        fetch(make_client(handler), "https://example.com/")
    assert exc.value.kind == "redirect"


def test_redirect_loop_gives_up():
    def handler(request):
        return httpx.Response(302, headers={"location": "/loop"}, request=request)

    with pytest.raises(FetchError) as exc:
        fetch(make_client(handler), "https://example.com/loop")
    assert exc.value.kind == "redirect"


def test_redirect_to_private_address_blocked(monkeypatch):
    """Guard must re-run on every redirect hop (SSRF via redirect)."""

    def guard(url):
        if "10.0.0.1" in url:
            raise GuardError("non-public address")
        return url

    monkeypatch.setattr("app.crawler.fetcher.assert_public_url", guard)

    def handler(request):
        if request.url.host == "safe.test":
            return httpx.Response(302, headers={"location": "http://10.0.0.1/secret"}, request=request)
        return httpx.Response(200, text="leak", request=request)

    with pytest.raises(GuardError):
        fetch(make_client(handler), "https://safe.test/")


# --- HTTP status mapping --------------------------------------------------


@pytest.mark.parametrize("code", [401, 403, 429])
def test_auth_and_rate_limit_map_to_blocked(code):
    def handler(request):
        return httpx.Response(code, request=request)

    with pytest.raises(FetchError) as exc:
        fetch(make_client(handler), "https://example.com/")
    assert exc.value.kind == "blocked"
    assert str(code) in exc.value.detail


def test_404_maps_to_http_error():
    def handler(request):
        return httpx.Response(404, request=request)

    with pytest.raises(FetchError) as exc:
        fetch(make_client(handler), "https://example.com/")
    assert exc.value.kind == "http"
    assert "HTTP 404" in exc.value.detail


def test_500_retries_then_fails():
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        return httpx.Response(500, request=request)

    with pytest.raises(FetchError) as exc:
        fetch(make_client(handler), "https://example.com/")
    assert exc.value.kind == "http"
    assert calls["n"] == 1 + config.RETRIES


def test_500_then_success_succeeds_on_retry():
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(503, request=request)
        return httpx.Response(200, html="<html>ok</html>", request=request)

    result = fetch(make_client(handler), "https://example.com/")
    assert result.status_code == 200
    assert calls["n"] == 2


# --- network failures -----------------------------------------------------


def test_timeout_maps_to_network_and_retries():
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        raise httpx.ConnectTimeout("timed out", request=request)

    with pytest.raises(FetchError) as exc:
        fetch(make_client(handler), "https://example.com/")
    assert exc.value.kind == "network"
    assert calls["n"] == 1 + config.RETRIES


def test_connection_error_then_success():
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        if calls["n"] == 1:
            raise httpx.ConnectError("refused", request=request)
        return httpx.Response(200, html="<html>ok</html>", request=request)

    result = fetch(make_client(handler), "https://example.com/")
    assert b"ok" in result.content


# --- content type / size limits -------------------------------------------


def test_non_html_content_type_rejected():
    def handler(request):
        return httpx.Response(
            200, content=b"%PDF-1.4", headers={"content-type": "application/pdf"}, request=request
        )

    with pytest.raises(FetchError) as exc:
        fetch(make_client(handler), "https://example.com/brochure")
    assert exc.value.kind == "not_html"


def test_non_html_allowed_when_not_expected():
    def handler(request):
        return httpx.Response(200, text="User-agent: *\nDisallow: /", request=request)

    result = fetch(make_client(handler), "https://example.com/robots.txt", expect_html=False)
    assert b"User-agent" in result.content


def test_oversized_body_rejected(monkeypatch):
    monkeypatch.setattr(config, "MAX_BYTES_PER_PAGE", 10)

    def handler(request):
        return httpx.Response(200, html="x" * 100, request=request)

    with pytest.raises(FetchError) as exc:
        fetch(make_client(handler), "https://example.com/")
    assert exc.value.kind == "too_large"


# --- robots.txt -----------------------------------------------------------


def test_missing_robots_allows_everything():
    def handler(request):
        return httpx.Response(404, request=request)

    rules = asyncio.run(load_robots(make_client(handler), "https://example.com", {}))
    assert rules.allowed("https://example.com/")
    assert rules.allowed("https://example.com/any")


def test_robots_disallow_respected():
    def handler(request):
        return httpx.Response(200, text="User-agent: *\nDisallow: /private", request=request)

    rules = asyncio.run(load_robots(make_client(handler), "https://example.com", {}))
    assert rules.allowed("https://example.com/")
    assert not rules.allowed("https://example.com/private/page")
    assert rules.allowed("https://example.com/about")


def test_robots_fail_open_on_server_error():
    def handler(request):
        return httpx.Response(500, request=request)

    rules = asyncio.run(load_robots(make_client(handler), "https://example.com", {}))
    assert rules.allowed("https://example.com/")


def test_robots_cached_per_origin():
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        return httpx.Response(404, request=request)

    cache = {}
    client = make_client(handler)
    asyncio.run(load_robots(client, "https://example.com/a", cache))
    asyncio.run(load_robots(client, "https://example.com/b", cache))
    assert calls["n"] == 1


def test_lying_content_length_header_rejected():
    """Server declares a huge body — reject before trusting the download."""
    def handler(request):
        return httpx.Response(
            200,
            content=b"<html>tiny</html>",
            headers={"content-length": "999999999"},
            request=request,
        )

    with pytest.raises(FetchError) as exc:
        fetch(make_client(handler), "https://example.com/")
    assert exc.value.kind == "too_large"


def test_invalid_content_length_falls_back_to_body_size(monkeypatch):
    monkeypatch.setattr(config, "MAX_BYTES_PER_PAGE", 10)

    def handler(request):
        return httpx.Response(
            200,
            content=b"x" * 100,
            headers={"content-length": "chunked"},
            request=request,
        )

    with pytest.raises(FetchError) as exc:
        fetch(make_client(handler), "https://example.com/")
    assert exc.value.kind == "too_large"
