"""Page through cci records in sequence order for bus.py, standing.py, ledger.py, and desk-runner.py, and read which PRs open `hold` records name."""

from __future__ import annotations

import json
from collections.abc import Callable

PAGE_BYTES = 16000
CLAIM_KINDS = ("opened", "head")


def records(run: Callable[[list[str]], str], drive: str, *filters: str, since: int | str = 0) -> list[dict]:
    """Every record on `drive` after `since`, a sequence or any `cci tail --since` time, that matches the `cci tail` filters, paged by sequence; touches no cursor."""
    found: list[dict] = []
    while True:
        out = run(["cci", "tail", "--drive", drive, "--since", str(since), "--json", "--budget", str(PAGE_BYTES), *filters])
        page = [json.loads(line) for line in out.splitlines() if line.strip()]
        if not page:
            return found
        found += page
        since = page[-1]["seq"]


def held_prs(run: Callable[[list[str]], str], drive: str) -> dict[str, int]:
    """Each PR an open `hold` record on `drive` names, mapped to that record's seq; a `lift --resolves <seq>` closes the record and frees the PR.

    `cci tail --kind=hold` also returns the caller's peer mail of any kind and drive, which carries no status, so only the drive's hold records count."""
    holds = [record for record in records(run, drive, "--kind=hold") if record["kind"] == "hold" and record["drive"] == drive]
    return {str(pr): record["seq"] for record in holds if record["status"] == "open" for pr in record["refs"].get("prs", [])}
