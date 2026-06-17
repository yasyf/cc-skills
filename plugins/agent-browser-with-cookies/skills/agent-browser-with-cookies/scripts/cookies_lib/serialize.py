"""Field conversions and the Playwright/agent-browser ``--state`` JSON builder.

agent-browser loads ``{"cookies": [...], "origins": []}`` via ``--state`` — the
standard Playwright storageState shape. We only carry cookies (the local cookie
store has no localStorage), so ``origins`` is always empty.
"""

from __future__ import annotations

import json
import os
import tempfile
from typing import Any

# Seconds between the Windows epoch (1601-01-01) and the Unix epoch (1970-01-01).
WINDOWS_EPOCH_OFFSET = 11_644_473_600

_SAMESITE = {2: "Strict", 1: "Lax", 0: "None", -1: "Lax"}


def chrome_micros_to_unix(expires_utc: int) -> float:
    """Chrome ``expires_utc`` (µs since 1601) → Unix seconds, or -1 for session cookies."""
    if expires_utc <= 0:
        return -1
    return expires_utc / 1_000_000 - WINDOWS_EPOCH_OFFSET


def samesite_str(value: int) -> str:
    """Chrome ``samesite`` int → Playwright string (unknown/unspecified → Lax)."""
    return _SAMESITE.get(value, "Lax")


def _finalize_samesite(same: str, secure: bool) -> tuple[str, bool]:
    same = str(same).capitalize()
    if same not in ("Strict", "Lax", "None"):
        same = "Lax"
    if same == "None":
        secure = True  # browsers reject SameSite=None without Secure
    return same, secure


def build_cookie(
    *,
    name: str,
    value: str,
    host_key: str,
    path: str | None,
    expires: float,
    secure: Any,
    http_only: Any,
    samesite: int,
) -> dict:
    """Build one Playwright-shaped cookie dict from decrypted Chrome columns."""
    same, secure_bool = _finalize_samesite(samesite_str(samesite), bool(secure))
    return {
        "name": name,
        "value": value,
        "domain": host_key,
        "path": path or "/",
        "expires": expires,
        "httpOnly": bool(http_only),
        "secure": secure_bool,
        "sameSite": same,
    }


def normalize_getcookie_record(rec: dict, request_host: str, scheme: str = "https") -> dict | None:
    """Normalize one ``@mherod/get-cookie`` JSON record into a Playwright cookie dict.

    get-cookie reliably emits name/value/domain; other attributes vary, so we
    default path=/ and secure-from-scheme and read the rest from a ``meta`` block
    when present.
    """
    name = rec.get("name")
    value = rec.get("value")
    if name is None or value is None:
        return None
    domain = rec.get("domain") or request_host
    path = rec.get("path") or "/"

    expires: float = -1
    raw = rec.get("expiry", rec.get("expires"))
    if isinstance(raw, bool):
        raw = None
    if isinstance(raw, (int, float)):
        expires = float(raw)
    elif isinstance(raw, str) and raw.strip().lstrip("-").isdigit():
        expires = float(raw)

    meta = rec.get("meta") or {}
    secure = bool(meta.get("secure", scheme == "https"))
    http_only = bool(meta.get("httpOnly", meta.get("httponly", False)))
    same, secure = _finalize_samesite(meta.get("sameSite") or meta.get("samesite") or "Lax", secure)
    return {
        "name": name,
        "value": value,
        "domain": domain,
        "path": path,
        "expires": expires,
        "httpOnly": http_only,
        "secure": secure,
        "sameSite": same,
    }


def build_state(cookies: list[dict]) -> dict:
    """Wrap cookies in the agent-browser ``--state`` envelope."""
    return {"cookies": cookies, "origins": []}


def write_state_file(state: dict, out_path: str | None = None) -> str:
    """Write the state JSON to a 0600 file (a private temp file unless ``out_path`` is given)."""
    data = json.dumps(state).encode("utf-8")
    if out_path:
        fd = os.open(out_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        os.chmod(out_path, 0o600)
        return out_path
    fd, path = tempfile.mkstemp(suffix=".json", prefix="abwc-state-")
    with os.fdopen(fd, "wb") as fh:
        fh.write(data)
    os.chmod(path, 0o600)
    return path
