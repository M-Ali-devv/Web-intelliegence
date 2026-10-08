"""Stop the crawler from being pointed at private/internal addresses.

Imported URLs are untrusted input. Without this guard a row containing
http://127.0.0.1:8080 or http://169.254.169.254/latest/meta-data/ would
make the crawler probe our own machine or cloud metadata endpoints (SSRF).
Every URL — including each redirect hop — must pass through here.
"""

import ipaddress
import socket

from urllib.parse import urlparse


class GuardError(Exception):
    """The address is not a public website we are allowed to fetch."""


def _is_public(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    return not (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    )


def assert_public_url(url: str) -> str:
    """Return the URL when its host resolves only to public addresses.

    Raises GuardError otherwise (bad host, private/loopback/link-local IP,
    or DNS failure).
    """
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise GuardError(f"unsupported scheme: {parsed.scheme or '(none)'}")

    host = parsed.hostname
    if not host:
        raise GuardError("URL has no host")

    try:
        infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        raise GuardError(f"cannot resolve host {host!r}") from exc
    if not infos:
        raise GuardError(f"cannot resolve host {host!r}")

    addresses = {info[4][0] for info in infos}
    for raw in addresses:
        ip = ipaddress.ip_address(raw.split("%")[0])
        if not _is_public(ip):
            raise GuardError(f"host {host!r} points at non-public address {ip}")

    return url
