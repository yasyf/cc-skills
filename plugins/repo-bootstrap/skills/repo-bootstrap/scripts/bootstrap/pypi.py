"""Check whether a PyPI distribution name is free.

Exit codes: 0 = AVAILABLE, 1 = TAKEN, 2 = UNKNOWN (verify manually), 3 = invalid.
"""

from __future__ import annotations

import urllib.error
import urllib.request

from .common import DIST_NAME_RE


def _http_status(url: str) -> int | str:
    try:
        with urllib.request.urlopen(url) as resp:  # noqa: S310 — fixed https host
            return resp.status
    except urllib.error.HTTPError as exc:
        return exc.code
    except Exception:
        return "000"


def main(name: str) -> int:
    if not DIST_NAME_RE.match(name):
        print(f"INVALID: {name} is not a valid PyPI project name")
        return 3

    status = _http_status(f"https://pypi.org/pypi/{name}/json")
    if status == 404:
        print("AVAILABLE")
        return 0
    if status == 200:
        print(f"TAKEN ({name} is an existing project)")
        return 1
    print(f"UNKNOWN: HTTP {status} — verify manually at https://pypi.org/project/{name}/")
    return 2
