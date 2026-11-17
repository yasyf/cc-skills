"""Alerts reach the lane drive on their own, by his rules for an incident: "a page always comes with 1) a channel message in a drive channel, and 2) in parallel needs to start working on a fix and be over communicative about it, ai-oncall style", and "if anything goes wrong while doing things autonomously, the agent should have a tool to use ddog to page its human". A Datadog monitor the drive's `incident_query` finds alerting with no lane on it opens an incident lane, whose owner starts the fix in the same call, with a diagnosis item on the work queue beside it; when he has been away, he is paged through the repository's page command and the page's line goes in the lane's channel."""

import json
import os
import urllib.parse
import urllib.request
from datetime import timedelta
from pathlib import Path

from lane import facts, store

AWAY_AFTER = timedelta(minutes=30)
ALERTING = ("Alert", "Warn")


def away(drive: str) -> bool:
    """Whether his newest words to any lane are older than AWAY_AFTER."""
    said = [store.parse(ask["at"]) for lane in store.load_all(drive).values() for ask in lane.get("asks", []) if ask.get("ts")]
    return not said or store.now() - max(said) > AWAY_AFTER


def page(drive: str, lane_id: str, why: str) -> str:
    """Page his own on-call user with the page command the drive checkout's providers name, so the drive holds no paging credential and can page nobody else, and post the page's line in the lane's channel; the command's line."""
    from drive import providers

    from lane import say

    lane = store.load(drive, lane_id)
    repo = providers.at(Path(store.config(drive)["checkout"])).repo
    if not repo.page_owner:
        raise store.Refused("the drive checkout's providers name no command that pages him")
    done = repo.run(f"{repo.page_owner} --title {{title}} --description {{description}}", title=f"{lane['title']}"[:80], description=why).strip()

    def paging(lane: dict) -> None:
        lane["incident"] = (lane.get("incident") or {}) | {"paged": store.stamp(), "paged_why": why}
        store.note(lane, f"paged him: {why} ({done})")

    store.update(drive, lane_id, paging, leaving_unreplied=True)
    line = f"🚨 Paged you: {why}"
    say.SENDER(drive, store.load(drive, lane_id), line, None, None) if store.config(drive)["posting"] else say.logged(drive, store.load(drive, lane_id), line, None, None)
    return done


def alerting(drive: str) -> list[dict]:
    """The monitors the drive's `incident_query` finds alerting now, by Datadog's monitor search."""
    if not (query := store.config(drive).get("incident_query")):
        return []
    keys = facts.datadog_keys(drive)
    site = os.environ.get("DD_SITE", "datadoghq.com")
    request = urllib.request.Request(f"https://api.{site}/api/v1/monitor/search?{urllib.parse.urlencode({'query': query, 'per_page': 100})}", headers={"DD-API-KEY": keys["DD_API_KEY"], "DD-APPLICATION-KEY": keys["DD_APPLICATION_KEY"]})
    with urllib.request.urlopen(request, timeout=20) as answer:
        found = json.loads(answer.read()).get("monitors") or []
    return [monitor for monitor in found if monitor.get("status") in ALERTING]


def intake(drive: str) -> list[str]:
    """Open an incident lane for each alerting monitor no open lane names, its owner started in the same call with no cap holding it, a diagnosis item queued beside it, and him paged when he is away; what it opened."""
    from lane import verbs, work

    shape = store.config(drive)
    held = {str((lane.get("incident") or {}).get("monitor")) for lane in store.load_all(drive).values() if (lane.get("incident") or {}).get("monitor") and not lane["incident"].get("fix_live")}
    opened = []
    for monitor in alerting(drive):
        if str(monitor["id"]) in held or f"incident-{monitor['id']}" in store.load_all(drive):
            continue
        lane_id = f"incident-{monitor['id']}"
        link = f"https://app.datadoghq.com/monitors/{monitor['id']}"
        words = f"Datadog monitor {monitor['id']} is {monitor['status']}: {monitor['name']}. {link}. Start the fix now, narrate it in this lane's channel as ai-oncall does, and tell the person who reported it once the fix is live."
        verbs.open_lane(drive, lane_id, monitor["name"][:80], words=words, channel=shape.get("incident_channel"), worktree=shape.get("incident_worktree") or shape["checkout"], incident={"monitor": str(monitor["id"]), "link": link, "opened": store.stamp()})
        work.add(drive, "ask", lane_id, f"diagnose monitor {monitor['id']}", f"Diagnose why Datadog monitor {monitor['id']} ({monitor['name']}, {link}) alerts: read its query, the logs, traces and metrics behind it with the Datadog tools, and the recent deploys and merges, and finish this item with the mechanism and the evidence for it, so the lane's owner can aim the fix. Change nothing.", by="incident intake")
        opened.append(lane_id)
        if away(drive):
            page(drive, lane_id, f"{monitor['name']} is {monitor['status']} ({link}); a fix lane and a diagnosis item started on it")
    return opened
