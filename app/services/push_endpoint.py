"""SSRF guard for Web Push endpoints.

A push subscription contains a URL that THIS SERVER later posts to. If any URL were accepted, a signed-in user
could make the server call internal addresses (localhost, 169.254.169.254, a router, another service on the
host). So an endpoint must pass every check below, when it is saved AND again right before each send
(DNS answers can change between the two):

  1. https only, no user:password@ part, no custom port (443 only), no fragment tricks, sane length.
  2. The host is a name, never an IP address literal.
  3. The host belongs to a known browser push service (allow-list; extend with PUSH_ENDPOINT_EXTRA_HOSTS).
  4. Every address the name resolves to is a normal public address (not private, loopback, link-local,
     reserved, multicast, unspecified, carrier-grade NAT, or an IPv4 address hidden inside an IPv6 one).

Redirects: see push_service._send_one (they are not followed where the installed pywebpush allows that setting).
"""
from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlsplit

from flask import current_app

MAX_ENDPOINT_LEN = 1000

# Host names (and everything below them) of the push services used by Chrome/Edge/Opera/Samsung (FCM),
# Firefox (Mozilla autopush), Safari (Apple) and legacy Edge/Windows (WNS).
DEFAULT_ALLOWED_HOSTS = (
    "fcm.googleapis.com",
    "android.googleapis.com",
    "push.services.mozilla.com",
    "push.apple.com",
    "notify.windows.com",
)


class EndpointRejected(ValueError):
    """The endpoint is not allowed. `code` is a short machine-readable reason (never shown with the URL)."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def allowed_hosts() -> tuple[str, ...]:
    extra = current_app.config.get("PUSH_ENDPOINT_EXTRA_HOSTS") or ()
    return tuple(DEFAULT_ALLOWED_HOSTS) + tuple(h.strip().lower().lstrip(".") for h in extra if h.strip())


def host_allowed(host: str) -> bool:
    return any(host == a or host.endswith("." + a) for a in allowed_hosts())


def is_public_address(raw: str) -> bool:
    try:
        ip = ipaddress.ip_address(raw.split("%", 1)[0])        # drop an IPv6 zone id such as fe80::1%eth0
    except ValueError:
        return False
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped                                    # ::ffff:127.0.0.1 is still 127.0.0.1
    return bool(ip.is_global and not ip.is_multicast)


def _resolve(host: str) -> list[str]:
    return [info[4][0] for info in socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)]


def check_endpoint(endpoint: str, resolver=None) -> str:
    """Return the cleaned endpoint or raise EndpointRejected. `resolver` is for tests (host -> list of IP strings)."""
    endpoint = (endpoint or "").strip()
    if not endpoint or len(endpoint) > MAX_ENDPOINT_LEN:
        raise EndpointRejected("length")
    try:
        parts = urlsplit(endpoint)
        port = parts.port                                      # raises ValueError for a bad port
    except ValueError:
        raise EndpointRejected("malformed") from None
    if parts.scheme != "https":
        raise EndpointRejected("scheme")
    if parts.username is not None or parts.password is not None or "@" in parts.netloc:
        raise EndpointRejected("userinfo")
    if port not in (None, 443):
        raise EndpointRejected("port")
    host = (parts.hostname or "").rstrip(".").lower()
    if not host or "\\" in endpoint or any(ch.isspace() or ord(ch) < 32 for ch in endpoint):
        raise EndpointRejected("host")
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass                                                   # a name, as required
    else:
        raise EndpointRejected("ip_literal")
    if not host_allowed(host):
        raise EndpointRejected("host_not_allowed")
    if current_app.config.get("PUSH_ENDPOINT_CHECK_DNS", True):
        try:
            addresses = (resolver or _resolve)(host)
        except OSError:
            raise EndpointRejected("dns") from None
        if not addresses or not all(is_public_address(a) for a in addresses):
            raise EndpointRejected("private_address")
    return endpoint
