from __future__ import annotations

import json
import re
import subprocess
import threading
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode

CCI_URL = "http://127.0.0.1:7377/v1"
GRAPHQL_FLOOR = 500
RATE_LIMITED = re.compile(r"\b429\b|rate.?limit", re.IGNORECASE)
RATE_LIMIT_QUERY = "rateLimit { remaining resetAt }"
QUOTA: dict = {"remaining": None, "reset_at": None}
QUOTA_LOCK = threading.Lock()


class RateLimited(RuntimeError):
    pass


def quota_refusal(moment: datetime) -> str | None:
    with QUOTA_LOCK:
        remaining, reset_at = QUOTA["remaining"], QUOTA["reset_at"]
    if remaining is not None and remaining < GRAPHQL_FLOOR and reset_at and moment < reset_at:
        return f"GitHub GraphQL quota is {remaining}, under {GRAPHQL_FLOOR}, until {reset_at:%H:%MZ}"
    return None


def record_quota(limit: dict) -> None:
    with QUOTA_LOCK:
        QUOTA.update(remaining=limit["remaining"], reset_at=datetime.fromisoformat(limit["resetAt"].replace("Z", "+00:00")))


def failure_text(failure: BaseException) -> str:
    detail = getattr(failure, "stderr", "") or str(failure)
    detail = detail.decode(errors="replace") if isinstance(detail, bytes) else detail
    last = (detail.strip().splitlines() or [type(failure).__name__])[-1]
    if isinstance(failure, subprocess.TimeoutExpired):
        last = f"timed out after {failure.timeout:g}s: {last}"
    elif isinstance(failure, subprocess.CalledProcessError):
        last = f"exit {failure.returncode}: {last}"
    return last[:300]


@dataclass
class Context:
    """What a component may read: the dashboard dir, the drive's facts, the clock, and bounded calls out."""

    dir: Path
    facts: dict
    now: datetime
    timeout: float
    prior: object | None = None
    lookup: Callable[[str], object | None] = lambda instance: None
    summary: Callable[[], list[dict]] = lambda: []

    def run(self, argv: list[str], timeout: float | None = None, cwd: str | Path | None = None, input: str | None = None) -> str:
        """Run a command and return its stdout; a non-zero exit raises, and a 429 or rate-limit message backs the card off."""
        try:
            return subprocess.run(argv, capture_output=True, text=True, check=True, cwd=cwd, input=input, timeout=timeout or self.timeout).stdout
        except subprocess.CalledProcessError as failure:
            if RATE_LIMITED.search(failure.stderr or ""):
                raise RateLimited(failure_text(failure)) from failure
            raise

    def json(self, argv: list[str], timeout: float | None = None, cwd: str | Path | None = None):
        return json.loads(self.run(argv, timeout, cwd) or "null")

    def gh_graphql(self, query: str, **variables) -> dict:
        """Run one GraphQL query through `gh api graphql`; it refuses while the last known quota is under 500."""
        if refusal := quota_refusal(self.now):
            raise RateLimited(refusal)
        argv = ["gh", "api", "graphql", "-f", f"query={query.rstrip().removesuffix('}')} {RATE_LIMIT_QUERY} }}"]
        for name, value in variables.items():
            argv += ["-F" if isinstance(value, int) and not isinstance(value, bool) else "-f", f"{name}={value}"]
        data = self.json(argv)["data"]
        record_quota(data.pop("rateLimit"))
        return data

    def ccn(self, *args: str) -> str:
        return self.run(["ccn", "-R", str(self.facts["checkout"]), *args])

    def cci(self, path: str, **params):
        """GET a cci daemon endpoint for the drive's cci drive; list values repeat their key."""
        query = urlencode({"drive": self.facts["cci_drive"]} | {key: value for key, value in params.items() if value is not None}, doseq=True)
        with urllib.request.urlopen(f"{CCI_URL}/{path}?{query}", timeout=self.timeout) as response:
            return json.load(response)

    def glob(self, pattern: str) -> list[Path]:
        """Matches newest first; a relative pattern resolves against the dashboard dir, `~` against home."""
        expanded = Path(pattern).expanduser()
        if expanded.is_absolute():
            root, relative = Path(expanded.anchor), str(expanded.relative_to(expanded.anchor))
        else:
            root, relative = self.dir, pattern
        return sorted((path for path in root.glob(relative) if path.is_file()), key=lambda path: path.stat().st_mtime, reverse=True)

    def latest(self, instance: str):
        """The last good payload of another card on this dashboard, by card id, or None."""
        return self.lookup(instance)

    def cards(self) -> list[dict]:
        """Every card on this dashboard with its status, age, run time and error, without payloads."""
        return self.summary()


def now() -> datetime:
    return datetime.now(timezone.utc)
