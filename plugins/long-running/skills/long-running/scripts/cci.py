"""Page through cci records in sequence order for bus.py, standing.py, ledger.py, and desk-runner.py."""

from __future__ import annotations

import json
from collections.abc import Callable

PAGE_BYTES = 16000


def records(run: Callable[[list[str]], str], drive: str, *filters: str, since: int = 0) -> list[dict]:
    """Every record on `drive` after sequence `since` that matches the `cci tail` filters, paged by sequence; touches no cursor."""
    found: list[dict] = []
    while True:
        out = run(["cci", "tail", "--drive", drive, "--since", str(since), "--json", "--budget", str(PAGE_BYTES), *filters])
        page = [json.loads(line) for line in out.splitlines() if line.strip()]
        if not page:
            return found
        found += page
        since = page[-1]["seq"]
