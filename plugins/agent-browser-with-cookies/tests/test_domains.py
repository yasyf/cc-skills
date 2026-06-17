"""cookie_applies send-rule + host normalization."""

from __future__ import annotations

import pytest
from cookies_lib import domains


@pytest.mark.parametrize(
    ("host_key", "request_host", "expected"),
    [
        # host-only cookies match exactly
        ("app.example.com", "app.example.com", True),
        ("app.example.com", "other.example.com", False),
        ("example.com", "example.com", True),
        # domain cookies (leading dot) match the base host and any subdomain
        (".example.com", "example.com", True),
        (".example.com", "app.example.com", True),
        (".example.com", "deep.app.example.com", True),
        # boundary: a look-alike domain must NOT match
        (".example.com", "evil-example.com", False),
        (".example.com", "notexample.com", False),
        # case-insensitive
        (".Example.com", "APP.example.com", True),
    ],
)
def test_cookie_applies(host_key, request_host, expected):
    assert domains.cookie_applies(host_key, request_host) is expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("https://app.example.com/dash?x=1", "app.example.com"),
        ("app.example.com", "app.example.com"),
        ("http://localhost:3000", "localhost"),
        (".example.com", "example.com"),
        ("https://user:pw@host.example.com:8443/p", "host.example.com"),
        ("HTTPS://APP.Example.COM", "app.example.com"),
    ],
)
def test_normalize_host(value, expected):
    assert domains.normalize_host(value) == expected


def test_url_scheme():
    assert domains.url_scheme("https://x.com") == "https"
    assert domains.url_scheme("http://x.com") == "http"
    assert domains.url_scheme("x.com") == "https"
