from __future__ import annotations

from livedash import Col, Context, Table, component

TONE = {"ok": "ok", "pending": "muted", "stale": "warn", "error": "bad", "hung": "bad"}


@component("dashboard-health", "Dashboard health", every="30s")
def dashboard_health(ctx: Context) -> Table:
    """Every card on this dashboard with its status, the age of its last good payload, its last run time, and its error."""
    rows = [
        {"key": card["id"], "card": card["title"], "use": card["use"], "status": card["status"], "as_of": card["as_of"], "ms": card["ms"], "error": card["error"], "tone": TONE[card["status"]]}
        for card in ctx.cards()
    ]
    rows.sort(key=lambda row: list(TONE).index(row["status"]), reverse=True)
    return Table([Col("card", "Card"), Col("use", "Component"), Col("status", "Status", "badge"), Col("as_of", "Data from", "age"), Col("ms", "Run ms", "num"), Col("error", "Error")], rows)
