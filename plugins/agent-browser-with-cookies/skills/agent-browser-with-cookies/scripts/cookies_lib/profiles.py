"""Chrome profile discovery and cookie-DB reads (no decryption here).

Counting and row reads use only the plaintext ``host_key`` column, so they never
touch the Keychain. The DB is copied to a temp dir before reading so a running
Chrome (which holds a lock and a WAL/journal sidecar) is never disturbed.
"""

from __future__ import annotations

import json
import shutil
import sqlite3
import tempfile
from pathlib import Path

from .domains import cookie_applies

CHROME_DIR = Path.home() / "Library" / "Application Support" / "Google" / "Chrome"

_ROW_COLUMNS = (
    "host_key, name, encrypted_value, path, expires_utc, is_secure, is_httponly, samesite"
)


def chrome_dir() -> Path:
    return CHROME_DIR


def _local_state() -> dict:
    path = chrome_dir() / "Local State"
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return {}


def profile_info() -> dict[str, dict]:
    """Map profile dir name → {email, name} from Local State ``profile.info_cache``."""
    cache = _local_state().get("profile", {}).get("info_cache", {})
    return {
        name: {
            "email": info.get("user_name", ""),
            "name": info.get("gaia_name", "") or info.get("name", ""),
        }
        for name, info in cache.items()
    }


def list_profile_dirs() -> list[str]:
    """Profile directories that actually have a Cookies DB, sorted by name."""
    base = chrome_dir()
    if not base.is_dir():
        return []
    return sorted(c.name for c in base.iterdir() if c.is_dir() and (c / "Cookies").is_file())


def cookies_db_path(profile: str) -> Path:
    return chrome_dir() / profile / "Cookies"


def _read_rows(profile: str) -> list[sqlite3.Row]:
    """Copy the Cookies DB (+ sidecars) to a temp dir and read every row from the copy."""
    db = cookies_db_path(profile)
    if not db.is_file():
        return []
    tmpdir = Path(tempfile.mkdtemp(prefix="abwc-cookies-"))
    try:
        copy = tmpdir / "Cookies"
        shutil.copy2(db, copy)
        for suffix in ("-wal", "-shm", "-journal"):
            side = Path(str(db) + suffix)
            if side.is_file():
                shutil.copy2(side, str(copy) + suffix)
        # Open the private copy normally (read-write allowed) so SQLite applies the
        # WAL/journal and we see the latest committed cookies.
        con = sqlite3.connect(str(copy))
        con.row_factory = sqlite3.Row
        try:
            return con.execute(f"SELECT {_ROW_COLUMNS} FROM cookies").fetchall()
        finally:
            con.close()
    except (OSError, sqlite3.Error):
        return []
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def count_applicable(profile: str, request_host: str) -> int:
    """How many cookies in ``profile`` would be sent to ``request_host`` (no decryption)."""
    return sum(1 for r in _read_rows(profile) if cookie_applies(r["host_key"], request_host))


def read_encrypted_rows(profile: str, request_host: str) -> list[dict]:
    """Applicable cookie rows with raw ``encrypted_value`` bytes (no decryption)."""
    out = []
    for r in _read_rows(profile):
        if not cookie_applies(r["host_key"], request_host):
            continue
        ev = r["encrypted_value"]
        out.append(
            {
                "host_key": r["host_key"],
                "name": r["name"],
                "encrypted_value": bytes(ev) if ev is not None else b"",
                "path": r["path"],
                "expires_utc": r["expires_utc"],
                "is_secure": r["is_secure"],
                "is_httponly": r["is_httponly"],
                "samesite": r["samesite"],
            }
        )
    return out
