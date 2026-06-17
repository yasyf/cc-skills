"""Field conversions, state-JSON shape, and get-cookie normalization."""

from __future__ import annotations

import json
import os
import stat

import pytest
from cookies_lib import serialize


@pytest.mark.parametrize(
    ("expires_utc", "expected"),
    [
        (0, -1),  # session cookie
        (-1, -1),
        (13_350_000_000_000_000, 13_350_000_000_000_000 / 1_000_000 - serialize.WINDOWS_EPOCH_OFFSET),
    ],
)
def test_chrome_micros_to_unix(expires_utc, expected):
    assert serialize.chrome_micros_to_unix(expires_utc) == expected


def test_chrome_micros_known_value():
    # 13_300_000_000_000_000 µs since 1601 → a 2022-ish Unix timestamp, positive.
    out = serialize.chrome_micros_to_unix(13_300_000_000_000_000)
    assert out == pytest.approx(1_655_526_400, abs=1)


@pytest.mark.parametrize(
    ("value", "expected"),
    [(2, "Strict"), (1, "Lax"), (0, "None"), (-1, "Lax"), (99, "Lax")],
)
def test_samesite_str(value, expected):
    assert serialize.samesite_str(value) == expected


def test_build_cookie_none_forces_secure():
    c = serialize.build_cookie(
        name="sid", value="v", host_key=".example.com", path=None,
        expires=-1, secure=0, http_only=1, samesite=0,
    )
    assert c["sameSite"] == "None"
    assert c["secure"] is True  # SameSite=None requires Secure
    assert c["httpOnly"] is True
    assert c["path"] == "/"
    assert c["domain"] == ".example.com"


def test_build_cookie_keeps_secure_flag():
    c = serialize.build_cookie(
        name="x", value="y", host_key="app.example.com", path="/api",
        expires=123.0, secure=1, http_only=0, samesite=1,
    )
    assert c == {
        "name": "x", "value": "y", "domain": "app.example.com", "path": "/api",
        "expires": 123.0, "httpOnly": False, "secure": True, "sameSite": "Lax",
    }


def test_build_state_shape():
    state = serialize.build_state([{"name": "a"}])
    assert state == {"cookies": [{"name": "a"}], "origins": []}


def test_normalize_getcookie_record():
    rec = {"name": "s", "value": "tok", "domain": ".x.com",
           "meta": {"secure": True, "httpOnly": True, "sameSite": "lax"}, "expiry": "1700000000"}
    out = serialize.normalize_getcookie_record(rec, "x.com", "https")
    assert out["name"] == "s" and out["value"] == "tok" and out["domain"] == ".x.com"
    assert out["expires"] == 1700000000.0
    assert out["secure"] is True and out["httpOnly"] is True and out["sameSite"] == "Lax"


def test_normalize_getcookie_defaults():
    out = serialize.normalize_getcookie_record({"name": "s", "value": "t"}, "x.com", "https")
    assert out["domain"] == "x.com" and out["path"] == "/" and out["expires"] == -1
    assert out["secure"] is True  # https default


def test_normalize_getcookie_missing_fields():
    assert serialize.normalize_getcookie_record({"value": "t"}, "x.com") is None
    assert serialize.normalize_getcookie_record({"name": "s"}, "x.com") is None


def test_write_state_file_is_0600(tmp_path):
    state = serialize.build_state([{"name": "a", "value": "b"}])
    path = serialize.write_state_file(state)
    try:
        assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
        assert json.loads(open(path).read()) == state
    finally:
        os.unlink(path)


def test_write_state_file_explicit_path(tmp_path):
    out = tmp_path / "s.json"
    path = serialize.write_state_file({"cookies": [], "origins": []}, str(out))
    assert path == str(out)
    assert stat.S_IMODE(os.stat(out).st_mode) == 0o600
