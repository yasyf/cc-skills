"""Alerts reach the lane drive on their own, by his rules for an incident: "a page always comes with 1) a channel message in a drive channel, and 2) in parallel needs to start working on a fix and be over communicative about it, ai-oncall style", and "if anything goes wrong while doing things autonomously, the agent should have a tool to use ddog to page its human". A Datadog monitor the drive's `incident_query` finds alerting with no lane on it opens an incident lane, whose owner starts the fix in the same call, with a diagnosis item on the work queue beside it; when he has been away, he is paged through the repository's page command and the page's line goes in the lane's channel."""

import json
import os
import urllib.parse
import urllib.request
from datetime import timedelta
from pathlib import Path

from lane import facts, store

ALERTING = ("Alert", "Warn")
SETTINGS = ".claude/long-running.toml"


def settings(drive: str) -> dict:
    """The `[incidents]` table of the drive checkout's .claude/long-running.toml, where his bar lives by his 2:55 PM ruling, not in the skill or the drive config: `query`, the Datadog monitor search whose alerting monitors open a lane; `channel`, where an incident lane posts; `away_minutes`, how old his newest words are before an incident pages him. With no table, intake is off and no incident pages him on its own."""
    import tomllib

    if not (checkout := store.config(drive).get("checkout")) or not (found := Path(checkout) / SETTINGS).is_file():
        return {}
    return tomllib.loads(found.read_text()).get("incidents") or {}


def away(drive: str) -> bool:
    """Whether his newest words to any lane are older than the settings' away_minutes; never, with no such setting."""
    if not (minutes := settings(drive).get("away_minutes")):
        return False
    said = [store.parse(ask["at"]) for lane in store.load_all(drive).values() for ask in lane.get("asks", []) if ask.get("ts")]
    return not said or store.now() - max(said) > timedelta(minutes=minutes)


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
    if not (query := settings(drive).get("query")):
        return []
    keys = facts.datadog_keys(drive)
    site = os.environ.get("DD_SITE", "datadoghq.com")
    request = urllib.request.Request(f"https://api.{site}/api/v1/monitor/search?{urllib.parse.urlencode({'query': query, 'per_page': 100})}", headers={"DD-API-KEY": keys["DD_API_KEY"], "DD-APPLICATION-KEY": keys["DD_APPLICATION_KEY"]})
    with urllib.request.urlopen(request, timeout=20) as answer:
        found = json.loads(answer.read()).get("monitors") or []
    return [monitor for monitor in found if monitor.get("status") in ALERTING]


def joined(drive: str, lane_id: str, monitor: dict) -> None:
    """An alerting monitor on a service an open incident lane already covers joins that lane, as a note its owner reads, never a second lane."""

    def joining(lane: dict) -> None:
        lane["incident"]["joined"] = [*lane["incident"].get("joined", []), str(monitor["id"])]
        store.note(lane, f"monitor {monitor['id']} ({monitor['name']}) is {monitor['status']} on a service this incident covers")
        if lane["state"] == store.RUNNING:
            with store.note_path(drive, lane_id).open("a") as note:
                note.write(f"Datadog monitor {monitor['id']} ({monitor['name']}) is {monitor['status']} on a service this incident covers: https://app.datadoghq.com/monitors/{monitor['id']}\n\n")

    store.update(drive, lane_id, joining, leaving_unreplied=True)


def intake(drive: str) -> list[str]:
    """Open an incident lane for each alerting monitor no open lane names, its owner started in the same call with no cap holding it, a diagnosis item queued beside it, and him paged when he is away; what it opened."""
    from lane import verbs, work

    shape, ruled = store.config(drive), settings(drive)
    open_lanes = {lane_id: lane for lane_id, lane in store.load_all(drive).items() if (lane.get("incident") or {}).get("monitor") and not lane["incident"].get("fix_live")}
    held = {str(lane["incident"]["monitor"]) for lane in open_lanes.values()} | {str(monitor) for lane in open_lanes.values() for monitor in lane["incident"].get("joined", [])}
    opened = []
    for monitor in alerting(drive):
        if str(monitor["id"]) in held or f"incident-{monitor['id']}" in store.load_all(drive):
            continue
        services = {tag for tag in monitor.get("tags") or [] if tag.startswith("service:")}
        if covering := next((lane_id for lane_id, lane in open_lanes.items() if services & set(lane["incident"].get("services") or [])), None):
            joined(drive, covering, monitor)
            held.add(str(monitor["id"]))
            continue
        lane_id = f"incident-{monitor['id']}"
        link = f"https://app.datadoghq.com/monitors/{monitor['id']}"
        words = f"Datadog monitor {monitor['id']} is {monitor['status']}: {monitor['name']}. {link}. Start the fix now, narrate it in this lane's channel as ai-oncall does, and tell the person who reported it once the fix is live."
        verbs.open_lane(drive, lane_id, monitor["name"][:80], words=words, channel=ruled.get("channel"), worktree=shape.get("incident_worktree") or shape["checkout"], incident={"monitor": str(monitor["id"]), "link": link, "opened": store.stamp(), "services": sorted(services)})
        open_lanes[lane_id] = store.load(drive, lane_id)
        work.add(drive, "ask", lane_id, f"diagnose monitor {monitor['id']}", f"Diagnose why Datadog monitor {monitor['id']} ({monitor['name']}, {link}) alerts: read its query, the logs, traces and metrics behind it with the Datadog tools, and the recent deploys and merges, and finish this item with the mechanism and the evidence for it, so the lane's owner can aim the fix. Change nothing.", by="incident intake")
        opened.append(lane_id)
        if away(drive):
            page(drive, lane_id, f"{monitor['name']} is {monitor['status']} ({link}); a fix lane and a diagnosis item started on it")
    return opened
