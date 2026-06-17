"""Touch ID gate + the 'Chrome Safe Storage' key read.

The Chrome Safe Storage item is a file-based Keychain item — its ACL dialog is
password-only and can never be Touch ID. So we put a Touch ID *consent gate* in
front of it: a tiny Swift helper (LAContext.evaluatePolicy) that needs no
entitlement. The actual key read still goes through Apple-signed /usr/bin/security,
whose "Always Allow" sticks permanently, so after the first run the read is silent
and the only per-run prompt is the Touch ID tap.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

KEYCHAIN_SERVICE = "Chrome Safe Storage"


class KeychainError(Exception):
    """The Safe Storage key could not be read, or the Touch ID gate was declined."""


def data_dir() -> Path:
    """Persistent plugin data dir (survives updates); cache fallback off-plugin."""
    base = os.environ.get("CLAUDE_PLUGIN_DATA")
    if base:
        return Path(base)
    cache = os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    return Path(cache) / "agent-browser-with-cookies"


def _compile_gate(swift_src: Path) -> Path | None:
    """Compile (once) and ad-hoc sign the Touch ID gate; return its path or None."""
    swiftc = shutil.which("swiftc")
    if not swiftc or not swift_src.is_file():
        return None
    bin_path = data_dir() / "bin" / "touchid-gate"
    if bin_path.is_file() and bin_path.stat().st_mtime >= swift_src.stat().st_mtime:
        return bin_path
    bin_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        subprocess.run(
            [swiftc, str(swift_src), "-framework", "Security",
             "-framework", "LocalAuthentication", "-o", str(bin_path)],
            check=True, capture_output=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    codesign = shutil.which("codesign")
    if codesign:  # stable ad-hoc identity for keychain/launch caching
        subprocess.run([codesign, "-s", "-", "-f", str(bin_path)], capture_output=True)
    return bin_path if bin_path.is_file() else None


def touchid_gate(swift_src: Path, reason: str) -> str:
    """Run the Touch ID gate. Returns 'ok' or 'unavailable'; raises on explicit decline.

    'unavailable' (no swiftc, no Touch ID hardware, headless/SSH, compile failure)
    means the caller should proceed without a biometric gate — the underlying
    ``security`` read still authorizes via its own dialog.
    """
    binp = _compile_gate(swift_src)
    if not binp:
        return "unavailable"
    env = {**os.environ, "ABWC_TOUCHID_REASON": reason}
    try:
        proc = subprocess.run([str(binp)], env=env, capture_output=True, timeout=120)
    except (OSError, subprocess.TimeoutExpired):
        return "unavailable"
    if proc.returncode == 0:
        return "ok"
    if proc.returncode == 2:
        return "unavailable"
    raise KeychainError("Touch ID authentication was cancelled")


def read_safe_storage_key() -> str:
    """Read the raw 'Chrome Safe Storage' password via /usr/bin/security.

    First run triggers the one-time ACL dialog (click Always Allow); silent after.
    """
    try:
        proc = subprocess.run(
            ["/usr/bin/security", "find-generic-password", "-w", "-s", KEYCHAIN_SERVICE],
            capture_output=True, text=True,
        )
    except OSError as exc:
        raise KeychainError(f"could not run /usr/bin/security: {exc}") from exc
    if proc.returncode != 0:
        raise KeychainError(
            "could not read 'Chrome Safe Storage' from the Keychain (denied or missing)"
        )
    key = proc.stdout.strip()
    if not key:
        raise KeychainError("'Chrome Safe Storage' returned an empty key")
    return key
