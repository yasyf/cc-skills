"""Host parsing and the cookie send-rule — what the browser would send to a host.

No public-suffix list: ``cookie_applies`` implements the actual domain-match the
browser uses, which is all we need to pick the cookies for one target host.
"""

from __future__ import annotations


def normalize_host(value: str) -> str:
    """Lowercase bare host from a URL or domain (strip scheme, path, query, port, leading dot)."""
    v = value.strip().lower()
    if "://" in v:
        v = v.split("://", 1)[1]
    v = v.split("/", 1)[0].split("?", 1)[0]
    if "@" in v:  # strip userinfo if a full URL with credentials slipped through
        v = v.split("@", 1)[1]
    if ":" in v:
        v = v.split(":", 1)[0]
    return v.strip(".")


def url_scheme(value: str, default: str = "https") -> str:
    """Scheme of a URL, or ``default`` for a bare domain."""
    if "://" in value:
        return value.split("://", 1)[0].lower()
    return default


def cookie_applies(host_key: str, request_host: str) -> bool:
    """Would a browser send a cookie with this ``host_key`` to ``request_host``?

    Domain cookies (leading dot) match the base host and any subdomain; host-only
    cookies match exactly. Mirrors the browser's own send rule.
    """
    hk = host_key.lower()
    rh = request_host.lower()
    if hk.startswith("."):
        return rh == hk[1:] or rh.endswith(hk)
    return rh == hk
