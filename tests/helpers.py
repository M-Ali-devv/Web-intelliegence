"""Helpers for building httpx mock transports (no real network in tests)."""

from __future__ import annotations

import httpx


def make_client(handler) -> httpx.AsyncClient:
    """AsyncClient whose requests are answered by `handler(request)`."""
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def html_client(pages: dict[str, str], robots: str | int = 404, default_status: int = 404):
    """Client serving {path: html_body} plus an optional robots.txt.

    `robots=404` means no robots.txt (fail-open). Pass a string to serve one.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/robots.txt":
            if isinstance(robots, str):
                return httpx.Response(200, text=robots, request=request)
            return httpx.Response(robots, text="", request=request)
        if path in pages:
            return httpx.Response(200, html=pages[path], request=request)
        return httpx.Response(default_status, text="not found", request=request)

    return make_client(handler)


def assert_status(result, expected: str) -> None:
    assert result.status == expected, f"expected {expected!r}, got {result.status!r} ({result.error})"
