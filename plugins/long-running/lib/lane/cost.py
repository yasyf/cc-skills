"""What each lane spends, from the transcripts of its owner and the fix jobs started for it: tokens in the last hour by model, and dollars an hour by the drive config's `prices`, dollars per million tokens of each kind per model, so his page shows cost per lane, by his words "$500/hour overnight; keep cutting cost and find out why". No prices configured, no dollars: the tokens stand."""

import json
from datetime import timedelta
from pathlib import Path

from lane import owner as owners
from lane import store

WINDOW = timedelta(hours=1)
TAIL_BYTES = 4_000_000
KINDS = {"input_tokens": "input", "output_tokens": "output", "cache_read_input_tokens": "cache_read", "cache_creation_input_tokens": "cache_write"}


def sessions(lane: dict) -> list[str]:
    return [found for found in [(lane.get("owner") or {}).get("session"), (lane.get("last_owner") or {}).get("session"), *(fix.get("session") for fix in lane.get("fixes", []))] if found]


def tokens(path: Path, since: float) -> dict[str, dict[str, int]]:
    """Tokens of each kind by model in the transcript's turns since, from its last TAIL_BYTES."""
    spent: dict[str, dict[str, int]] = {}
    with path.open("rb") as transcript:
        transcript.seek(max(0, path.stat().st_size - TAIL_BYTES))
        for line in transcript.read().decode(errors="replace").splitlines():
            if '"usage"' not in line or not line.startswith("{"):
                continue
            row = json.loads(line)
            message = row.get("message") or {}
            if not (usage := message.get("usage")) or not row.get("timestamp") or store.parse(row["timestamp"]).timestamp() < since:
                continue
            kept = spent.setdefault(message.get("model") or "unknown", {})
            for field, kind in KINDS.items():
                kept[kind] = kept.get(kind, 0) + (usage.get(field) or 0)
    return spent


def hourly(drive: str, lane: dict) -> dict:
    """The lane's last-hour tokens by model and, with prices, its dollars an hour."""
    since = (store.now() - WINDOW).timestamp()
    spent: dict[str, dict[str, int]] = {}
    for session in dict.fromkeys(sessions(lane)):
        if (path := owners.transcript(session)) and path.stat().st_mtime >= since:
            for model, kinds in tokens(path, since).items():
                kept = spent.setdefault(model, {})
                for kind, count in kinds.items():
                    kept[kind] = kept.get(kind, 0) + count
    prices = store.config(drive).get("prices") or {}
    priced = [(prices[name], kinds) for model, kinds in spent.items() if (name := next((name for name in prices if name in model), None))]
    dollars = round(sum(price.get(kind, 0) * count / 1_000_000 for price, kinds in priced for kind, count in kinds.items()), 2) if priced else None
    return {"tokens": spent, "dollars_per_hour": dollars}
