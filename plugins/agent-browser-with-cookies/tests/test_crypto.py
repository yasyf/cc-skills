"""Decryption round-trip: encrypt like Chrome (v10 + 32-byte domain hash + PKCS7),
then assert the decryptor recovers the value. Plus v20 + wrong-key rejection."""

from __future__ import annotations

import hashlib

import pytest
from cookies_lib import crypto
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

PASSWORD = "s3cr3tSafeStorageKey=="
KEY = crypto.derive_key(PASSWORD)


def chrome_encrypt(value: str, key: bytes, host_key: str, *, domain_hash: bool = True) -> bytes:
    """Mimic Chrome v10 macOS: optional sha256(host) prefix, PKCS7 pad, AES-128-CBC, 'v10'."""
    plain = value.encode("utf-8")
    if domain_hash:
        plain = hashlib.sha256(host_key.encode("utf-8")).digest() + plain
    pad = 16 - (len(plain) % 16)
    plain += bytes([pad]) * pad
    enc = Cipher(algorithms.AES(key), modes.CBC(crypto.IV)).encryptor()
    return b"v10" + enc.update(plain) + enc.finalize()


@pytest.mark.parametrize(
    ("value", "host_key"),
    [
        ("abc123sessiontoken", ".example.com"),
        ("a" * 200, "app.example.com"),  # long, crosses block boundaries
        ("x", ".test.org"),  # short
        ("", ".empty.com"),  # empty value with a domain-hash prefix
    ],
)
def test_roundtrip_with_domain_hash(value, host_key):
    enc = chrome_encrypt(value, KEY, host_key, domain_hash=True)
    assert crypto.decrypt_value(enc, KEY, host_key) == value


def test_roundtrip_without_domain_hash():
    # Older value (no 32-byte prefix) must still decode cleanly.
    enc = chrome_encrypt("plainoldcookie", KEY, ".legacy.com", domain_hash=False)
    assert crypto.decrypt_value(enc, KEY, ".legacy.com") == "plainoldcookie"


def test_v20_rejected():
    with pytest.raises(crypto.DecryptError, match="v20"):
        crypto.decrypt_value(b"v20" + b"\x00" * 32, KEY, ".example.com")


def test_empty_value_is_empty_string():
    assert crypto.decrypt_value(b"", KEY, ".example.com") == ""


def test_wrong_key_raises():
    enc = chrome_encrypt("realtoken", KEY, ".example.com", domain_hash=True)
    wrong = crypto.derive_key("a-different-password")
    with pytest.raises(crypto.DecryptError):
        crypto.decrypt_value(enc, wrong, ".example.com")


def test_bad_block_size_raises():
    with pytest.raises(crypto.DecryptError):
        crypto.decrypt_value(b"v10" + b"\x01\x02\x03", KEY, ".example.com")
