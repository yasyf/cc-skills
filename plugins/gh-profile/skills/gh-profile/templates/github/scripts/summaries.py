"""Claude-maintained summaries sidecar — the read side, committed into consumer repos.

A consumer repo commits this module next to its render script and a scheduled
Claude pass (the repo-summaries plugin's ``refresh`` skill) regenerates the
sidecar whole; the render script only ever READS it through this module.

Sidecar schema:

    {"version": 1, "generated_at": "<ISO-8601 Z>",
     "<group>": {"<key>": {"as_of": "...", "summary": "..."}}}

Freshness is whole-file: summaries render only while the top-level
``generated_at`` is within ``stale_days`` (default ``SUMMARY_STALE_DAYS``) —
if the Claude pass dies, every line degrades to its plain form at once. A
missing sidecar is the normal no-Claude steady state; a malformed one warns
and renders plain. Entries are sanitized (first line, collapsed whitespace,
comment-safe, capped) so a bad sidecar can never corrupt the consumer's
rendered output.

STDLIB ONLY. Consumer scripts run on bare CI runners.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

SUMMARY_MAX_LEN = 120
SUMMARY_STALE_DAYS = 10


def parse_iso(stamp: str) -> datetime | None:
    """Parse an ISO-8601 timestamp ('Z' suffix tolerated); None on garbage."""
    try:
        return datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None


def load_summaries(path: Path) -> dict:
    """The sidecar dict, or {} when absent (the normal no-Claude steady state,
    silent) or unreadable (warned — a broken sidecar must never block a run)."""
    try:
        raw = json.loads(path.read_text())
    except FileNotFoundError:
        return {}
    except (OSError, ValueError):
        print(f"WARN: unreadable summaries at {path}; rendering without them", file=sys.stderr)
        return {}
    if not isinstance(raw, dict):
        print(f"WARN: unreadable summaries at {path}; rendering without them", file=sys.stderr)
        return {}
    return raw


def summaries_fresh(summaries: dict, now: datetime, stale_days: int = SUMMARY_STALE_DAYS) -> bool:
    """The whole file ages out together: if the scheduled Claude pass stops
    bumping generated_at, every summary degrades to a plain line at once. A
    naive stamp counts as UTC (the sidecar is LLM-written — drift must
    degrade, not crash); a stamp more than a day in the future counts as
    broken, not immortal."""
    generated = parse_iso(summaries.get("generated_at", ""))
    if generated is None:
        return False
    if generated.tzinfo is None:
        generated = generated.replace(tzinfo=timezone.utc)
    return timedelta(days=-1) <= now - generated <= timedelta(days=stale_days)


def clean_summary(value: object) -> str:
    """First line, collapsed whitespace, comment-safe, capped — the sidecar is
    repo content anyone could edit, so it never gets to break the consumer's
    marker splicing."""
    if not isinstance(value, str) or not value.strip():
        return ""
    line = " ".join(value.strip().splitlines()[0].split())
    if "<!--" in line or "-->" in line:
        return ""
    return line[:SUMMARY_MAX_LEN].rstrip()


def summary_for(
    summaries: dict | None, group: str, key: str, now: datetime, stale_days: int = SUMMARY_STALE_DAYS
) -> str:
    if not summaries or not summaries_fresh(summaries, now, stale_days):
        return ""
    entries = summaries.get(group)
    entry = entries.get(key) if isinstance(entries, dict) else None
    if not isinstance(entry, dict):
        return ""
    return clean_summary(entry.get("summary"))
