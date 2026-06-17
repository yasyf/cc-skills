"""Chrome macOS cookie decryption (the ``v10`` scheme).

key  = PBKDF2-HMAC-SHA1(safe_storage_password, b"saltysalt", 1003, dklen=16)
value = AES-128-CBC(key, iv=16x 0x20) over encrypted_value[3:], PKCS7-unpadded,
        then strip the 32-byte SHA256(host_key) domain-hash prefix Chrome v24+
        prepends (verified by hash match, which also catches a wrong key).

``v20`` (app-bound) values cannot be decrypted with the Safe Storage key and are
rejected so the caller can fall back to another engine.
"""

from __future__ import annotations

import hashlib

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

SALT = b"saltysalt"
ITERATIONS = 1003
KEY_LENGTH = 16
IV = b"\x20" * 16  # 16 space bytes, on macOS and Linux


class DecryptError(Exception):
    """A single cookie value could not be decrypted (v20, malformed, or wrong key)."""


def derive_key(safe_storage_password: str) -> bytes:
    """Derive the 16-byte AES key from the raw 'Chrome Safe Storage' string."""
    return hashlib.pbkdf2_hmac(
        "sha1", safe_storage_password.encode("utf-8"), SALT, ITERATIONS, dklen=KEY_LENGTH
    )


def _pkcs7_unpad(data: bytes) -> bytes:
    if not data:
        raise DecryptError("empty plaintext")
    pad = data[-1]
    if pad < 1 or pad > 16 or pad > len(data):
        raise DecryptError(f"bad PKCS7 padding length {pad}")
    if data[-pad:] != bytes([pad]) * pad:
        raise DecryptError("inconsistent PKCS7 padding")
    return data[:-pad]


def _strip_domain_hash(plain: bytes, host_key: str) -> bytes:
    """Strip Chrome v24+'s 32-byte SHA256(domain) prefix, verified against host_key.

    If the prefix matches we strip it with confidence. If it doesn't match but the
    whole buffer is valid UTF-8, assume an older value with no prefix. Otherwise
    strip 32 bytes as a best effort.
    """
    if len(plain) < 32:
        return plain
    head = plain[:32]
    for candidate in (host_key, host_key.lstrip(".")):
        if head == hashlib.sha256(candidate.encode("utf-8")).digest():
            return plain[32:]
    try:
        plain.decode("utf-8")
        return plain
    except UnicodeDecodeError:
        return plain[32:]


def decrypt_value(encrypted: bytes, key: bytes, host_key: str) -> str:
    """Decrypt one Chrome cookie ``encrypted_value``. Raise ``DecryptError`` on failure."""
    if not encrypted:
        return ""
    prefix = encrypted[:3]
    if prefix == b"v20":
        raise DecryptError("v20 app-bound cookie (not decryptable with the Safe Storage key)")
    if prefix == b"v10":
        ciphertext = encrypted[3:]
    else:
        # Legacy unencrypted value (no version tag) — return verbatim.
        try:
            return encrypted.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise DecryptError("unrecognized cookie encoding") from exc
    if not ciphertext or len(ciphertext) % 16 != 0:
        raise DecryptError("ciphertext is not a positive multiple of the block size")
    decryptor = Cipher(algorithms.AES(key), modes.CBC(IV)).decryptor()
    plain = _pkcs7_unpad(decryptor.update(ciphertext) + decryptor.finalize())
    plain = _strip_domain_hash(plain, host_key)
    try:
        return plain.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise DecryptError("decrypted value is not valid UTF-8 (likely wrong key)") from exc
