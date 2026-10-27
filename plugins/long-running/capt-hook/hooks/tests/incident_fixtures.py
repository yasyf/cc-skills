from __future__ import annotations

from datetime import UTC, datetime, timedelta


def opened(hours_ago: float) -> str:
    return (datetime.now(UTC) - timedelta(hours=hours_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")


def stored(incident_id: str, status: str = "open", hours_ago: float = 1, **facts: str) -> dict:
    return {"incident_id": incident_id, "status": status, "opened_at": opened(hours_ago), "facts": facts}


OUTAGE_0820 = stored("0820", "fix-pending", target="executor", alert="Datadog 333856815")
SAND_NOT_MIGRATED = stored("sand-not-migrated", target="sand", alert="Iris not_migrated refusals")
CLOSED_0820 = OUTAGE_0820 | {"status": "closed"}
RECOVERED_0820 = OUTAGE_0820 | {"status": "recovered"}
STALE_0820 = stored("0820", "open", hours_ago=7, target="executor")
OUTAGE_TWO = {"seq": 102819, "kind": "incident", "lane": "root", "topic": "outage-two", "refs": {"targets": ["executor"]}}
BARE_INCIDENT = {"seq": 103958, "kind": "incident", "lane": "alerts-desk", "topic": "re #103827", "refs": {"targets": None}}
INCIDENT_CLOSED = {"seq": 103990, "kind": "decide", "lane": "root", "text": "Incident closed: both fixes live."}
EARLIER_CLOSEOUT = INCIDENT_CLOSED | {"seq": 103900}
