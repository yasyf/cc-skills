"""Fallback engine: @mherod/get-cookie, swept across ALL browsers.

Used only when Chrome self-decrypt finds nothing — e.g. the user is logged in via
Brave/Arc/Edge/Safari/Firefox, or the cookies are app-bound (v20). We deliberately
do NOT pass ``--browser`` so get-cookie queries every browser. It is lazily
installed once into the persistent plugin data dir (it needs the native
better-sqlite3 module), then reused; ``bunx`` is the last-resort path.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

from .keychain import data_dir

GETCOOKIE_VERSION = "4.4.3"
PACKAGE = f"@mherod/get-cookie@{GETCOOKIE_VERSION}"


class GetCookieError(Exception):
    """The get-cookie fallback could not run or its output could not be parsed."""


def _cached_cli() -> Path | None:
    cli = data_dir() / "node_modules" / "@mherod" / "get-cookie" / "dist" / "cli.cjs"
    return cli if cli.is_file() else None


def _ensure_installed() -> Path | None:
    """Lazily ``bun add`` get-cookie into the data dir (builds better-sqlite3). Cached."""
    cli = _cached_cli()
    if cli:
        return cli
    bun = shutil.which("bun")
    if not bun:
        return None
    data = data_dir()
    data.mkdir(parents=True, exist_ok=True)
    pkg = data / "package.json"
    if not pkg.is_file():
        pkg.write_text('{"name":"abwc-getcookie-cache","private":true}\n')
    try:
        subprocess.run(
            [bun, "add", PACKAGE], cwd=str(data), check=True, capture_output=True, timeout=300
        )
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return None
    return _cached_cli()


def _command(request_host: str) -> list[str] | None:
    cli = _ensure_installed()
    bun = shutil.which("bun")
    if cli and bun:
        return [bun, str(cli), "%", request_host, "--output", "json"]
    bunx = shutil.which("bunx")
    if bunx:
        return [bunx, PACKAGE, "%", request_host, "--output", "json"]
    return None


def _parse(stdout: str) -> list[dict]:
    out = stdout.strip()
    if not out:
        return []
    try:
        data = json.loads(out)
    except json.JSONDecodeError:
        # Tolerate leading log noise by parsing from the first array/object bracket.
        start = min((i for i in (out.find("["), out.find("{")) if i != -1), default=-1)
        if start == -1:
            raise GetCookieError("could not parse get-cookie JSON output")
        try:
            data = json.loads(out[start:])
        except json.JSONDecodeError as exc:
            raise GetCookieError("could not parse get-cookie JSON output") from exc
    if isinstance(data, dict):
        data = data.get("cookies") or data.get("data") or [data]
    return data if isinstance(data, list) else []


def fetch_cookies(request_host: str) -> list[dict]:
    """Run get-cookie across all browsers for ``request_host``; return raw JSON records."""
    cmd = _command(request_host)
    if not cmd:
        raise GetCookieError("neither a cached get-cookie nor bun/bunx is available")
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise GetCookieError(f"get-cookie failed to run: {exc}") from exc
    return _parse(proc.stdout)
