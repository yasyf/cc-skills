"""CLI: argparse validation + extract orchestration with the system boundaries faked.

No real Keychain / Chrome / get-cookie: every boundary (profiles, keychain, getcookie)
is monkeypatched, and cookies are encrypted in-test with a known key.
"""

from __future__ import annotations

import hashlib
import json
import os

import cookies
import pytest
from cookies_lib import crypto
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

PASSWORD = "test-safe-storage-key"
KEY = crypto.derive_key(PASSWORD)


def _enc(value: str, host_key: str) -> bytes:
    plain = hashlib.sha256(host_key.encode()).digest() + value.encode()
    pad = 16 - (len(plain) % 16)
    plain += bytes([pad]) * pad
    e = Cipher(algorithms.AES(KEY), modes.CBC(crypto.IV)).encryptor()
    return b"v10" + e.update(plain) + e.finalize()


def _row(name, value, host_key=".example.com", **kw):
    return {
        "host_key": host_key, "name": name, "encrypted_value": _enc(value, host_key),
        "path": kw.get("path", "/"), "expires_utc": kw.get("expires_utc", 0),
        "is_secure": kw.get("is_secure", 1), "is_httponly": kw.get("is_httponly", 1),
        "samesite": kw.get("samesite", 1),
    }


def _run(monkeypatch, argv):
    monkeypatch.setattr("sys.argv", ["cookies.py", *argv])
    return cookies.main()


# --- argparse validation -----------------------------------------------------


@pytest.mark.parametrize(
    "argv",
    [[], ["extract"], ["extract", "--url", "https://x.com", "--domain", "x.com"],
     ["extract", "--url", "https://x.com", "--profile", "P", "--auto"]],
    ids=["no-subcommand", "extract-no-target", "extract-both-targets", "extract-profile-and-auto"],
)
def test_bad_args_exit_2(monkeypatch, argv):
    with pytest.raises(SystemExit) as exc:
        _run(monkeypatch, argv)
    assert exc.value.code == 2


# --- self-decrypt happy path -------------------------------------------------


def test_extract_self_decrypt(monkeypatch, capsys):
    monkeypatch.setattr(cookies.profiles, "list_profile_dirs", lambda: ["Profile 3"])
    monkeypatch.setattr(cookies.profiles, "count_applicable", lambda p, h: 3)
    monkeypatch.setattr(
        cookies.profiles, "read_encrypted_rows",
        lambda p, h: [_row("sid", "tok123"), _row("csrf", "abc", samesite=2)],
    )
    monkeypatch.setattr(cookies.keychain, "touchid_gate", lambda src, reason: "ok")
    monkeypatch.setattr(cookies.keychain, "read_safe_storage_key", lambda: PASSWORD)

    assert _run(monkeypatch, ["extract", "--url", "https://app.example.com"]) == 0
    out = capsys.readouterr()
    path = out.out.strip().splitlines()[-1]
    try:
        state = json.loads(open(path).read())
        names = {c["name"]: c["value"] for c in state["cookies"]}
        assert names == {"sid": "tok123", "csrf": "abc"}
        assert state["origins"] == []
        assert "engine=self" in out.err
    finally:
        os.unlink(path)


def test_extract_ambiguous_profiles_errors(monkeypatch):
    monkeypatch.setattr(cookies.profiles, "list_profile_dirs", lambda: ["Profile 3", "Profile 4"])
    monkeypatch.setattr(cookies.profiles, "count_applicable", lambda p, h: 10)  # tie
    monkeypatch.setattr(cookies.profiles, "profile_info", lambda: {})
    with pytest.raises(SystemExit) as exc:
        _run(monkeypatch, ["extract", "--url", "https://app.example.com"])
    assert "AMBIGUOUS" in str(exc.value)


# --- fallback path -----------------------------------------------------------


def test_extract_falls_back_to_getcookie(monkeypatch, capsys):
    monkeypatch.setattr(cookies.profiles, "list_profile_dirs", lambda: ["Profile 3"])
    monkeypatch.setattr(cookies.profiles, "count_applicable", lambda p, h: 0)  # Chrome has nothing
    called = {}

    def fake_fetch(host):
        called["host"] = host
        return [{"name": "auth", "value": "fromBrave", "domain": ".x.com",
                 "meta": {"secure": True, "sameSite": "lax"}}]

    monkeypatch.setattr(cookies.getcookie, "fetch_cookies", fake_fetch)

    assert _run(monkeypatch, ["extract", "--url", "https://x.com"]) == 0
    out = capsys.readouterr()
    assert called["host"] == "x.com"
    path = out.out.strip().splitlines()[-1]
    try:
        state = json.loads(open(path).read())
        assert state["cookies"][0] == {
            "name": "auth", "value": "fromBrave", "domain": ".x.com", "path": "/",
            "expires": -1, "httpOnly": False, "secure": True, "sameSite": "Lax",
        }
        assert "engine=get-cookie" in out.err
    finally:
        os.unlink(path)


def test_no_fallback_when_disabled(monkeypatch):
    monkeypatch.setattr(cookies.profiles, "list_profile_dirs", lambda: ["Profile 3"])
    monkeypatch.setattr(cookies.profiles, "count_applicable", lambda p, h: 0)
    monkeypatch.setattr(
        cookies.getcookie, "fetch_cookies",
        lambda h: pytest.fail("fallback should not run with --no-fallback"),
    )
    with pytest.raises(SystemExit) as exc:
        _run(monkeypatch, ["extract", "--url", "https://x.com", "--no-fallback"])
    assert "not logged into" in str(exc.value)


def test_keychain_cancel_then_no_fallback_errors(monkeypatch):
    monkeypatch.setattr(cookies.profiles, "list_profile_dirs", lambda: ["Profile 3"])
    monkeypatch.setattr(cookies.profiles, "count_applicable", lambda p, h: 2)
    monkeypatch.setattr(cookies.profiles, "read_encrypted_rows", lambda p, h: [_row("sid", "t")])

    def boom(src, reason):
        raise cookies.keychain.KeychainError("Touch ID authentication was cancelled")

    monkeypatch.setattr(cookies.keychain, "touchid_gate", boom)
    with pytest.raises(SystemExit) as exc:
        _run(monkeypatch, ["extract", "--url", "https://app.example.com", "--engine", "self"])
    assert "not logged into" in str(exc.value)
