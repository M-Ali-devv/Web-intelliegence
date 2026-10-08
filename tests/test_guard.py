"""SSRF guard: the crawler must never fetch private or internal addresses."""

import socket

import pytest

from app.crawler.guard import GuardError, assert_public_url


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1:8080/admin",
        "http://10.0.0.5/",
        "http://172.16.0.1/",
        "http://192.168.1.1/router",
        "http://169.254.169.254/latest/meta-data/",  # cloud metadata
        "http://[::1]/",
        "http://0.0.0.0/",
        "http://localhost/",
    ],
)
def test_blocks_private_and_internal_addresses(url):
    with pytest.raises(GuardError):
        assert_public_url(url)


@pytest.mark.parametrize("url", ["ftp://example.com", "file:///etc/passwd", "gopher://x/1"])
def test_blocks_non_http_schemes(url):
    with pytest.raises(GuardError, match="unsupported scheme"):
        assert_public_url(url)


def test_blocks_url_without_host():
    with pytest.raises(GuardError):
        assert_public_url("https://")


def test_blocks_dns_failure(monkeypatch):
    def fail(*_args, **_kwargs):
        raise socket.gaierror(socket.EAI_NONAME, "no such host")

    monkeypatch.setattr(socket, "getaddrinfo", fail)
    with pytest.raises(GuardError, match="cannot resolve"):
        assert_public_url("https://does-not-exist.invalid")


def test_blocks_hostname_resolving_to_private_ip(monkeypatch):
    def resolve(*_args, **_kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.1.2.3", 0))]

    monkeypatch.setattr(socket, "getaddrinfo", resolve)
    with pytest.raises(GuardError, match="non-public"):
        assert_public_url("https://sneaky.example")


def test_blocks_when_any_resolution_is_private(monkeypatch):
    def resolve(*_args, **_kwargs):
        return [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0)),
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("192.168.0.10", 0)),
        ]

    monkeypatch.setattr(socket, "getaddrinfo", resolve)
    with pytest.raises(GuardError, match="non-public"):
        assert_public_url("https://rebind.example")


def test_allows_public_ip(monkeypatch):
    def resolve(*_args, **_kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0))]

    monkeypatch.setattr(socket, "getaddrinfo", resolve)
    assert assert_public_url("https://example.com/page") == "https://example.com/page"


def test_allows_public_ipv6(monkeypatch):
    def resolve(*_args, **_kwargs):
        return [(socket.AF_INET6, socket.SOCK_STREAM, 6, "", ("2606:2800:220:1:248:1893:25c8:1946", 0, 0, 0))]

    monkeypatch.setattr(socket, "getaddrinfo", resolve)
    assert assert_public_url("https://example.com") is not None


def test_blocks_empty_dns_answer(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *_a, **_k: [])
    with pytest.raises(GuardError, match="cannot resolve"):
        assert_public_url("https://empty-answer.example")
