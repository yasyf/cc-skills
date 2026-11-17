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


TRIAGED = "incidents.json"
LOG = "incidents.log"


def triaged(drive: str) -> dict[str, dict]:
    path = store.drive_dir(drive) / TRIAGED
    return json.loads(path.read_text()) if path.is_file() else {}


def mark(drive: str, monitor_id: str, row: dict) -> None:
    with store.locked(drive):
        kept = triaged(drive)
        kept[monitor_id] = (kept.get(monitor_id) or {}) | row
        fresh = (store.drive_dir(drive) / TRIAGED).with_name(f".{TRIAGED}.{os.getpid()}.tmp")
        fresh.write_text(json.dumps(kept, indent=1) + "\n")
        fresh.replace(store.drive_dir(drive) / TRIAGED)


def open_incidents(drive: str) -> dict[str, dict]:
    return {lane_id: lane for lane_id, lane in store.load_all(drive).items() if (lane.get("incident") or {}).get("monitor") and not lane["incident"].get("fix_live")}


def intake(drive: str) -> list[str]:
    """Start one triage for each alert not triaged yet, by his words "it should triage each one and determine if relevant, not just blindly fix whatever comes in": an alerting monitor no open incident lane names, once per time it triggered. A cheap model reads it and decides, by `lane incident-triage`, to open a lane, join an open one, or drop it with why; nothing opens or pages before that. What it started."""
    import subprocess

    from lane import session

    held = {str(lane["incident"]["monitor"]) for lane in open_incidents(drive).values()} | {str(monitor) for lane in open_incidents(drive).values() for monitor in lane["incident"].get("joined", [])}
    seen, started = triaged(drive), []
    for monitor in alerting(drive):
        key, fired = str(monitor["id"]), str(monitor.get("last_triggered_ts") or "")
        if key in held or (seen.get(key) or {}).get("fired") == fired:
            continue
        mark(drive, key, {"fired": fired, "name": monitor["name"], "status": monitor["status"], "started": store.stamp(), "verdict": None})
        lane = store.launcher(drive)
        lanes = {lane_id: {"title": open_lane["title"], "monitor": open_lane["incident"]["monitor"], "services": open_lane["incident"].get("services")} for lane_id, open_lane in open_incidents(drive).items()}
        prompt = (
            f"Triage one Datadog alert for drive {drive}, by his words \"it should triage each one and determine if relevant, not just blindly fix whatever comes in\". The monitor: {json.dumps({key: monitor.get(key) for key in ('id', 'name', 'status', 'tags', 'query', 'last_triggered_ts')}, ensure_ascii=False)}, https://app.datadoghq.com/monitors/{monitor['id']}. "
            f"Open incident lanes: {json.dumps(lanes, ensure_ascii=False)}. Read the monitor, its message and its last few days of state with the Datadog tools: whether it is real now, whether real users are failing because of it, whether it flaps or is known noisy, and whether an open lane already covers it. "
            f"Then run exactly one: `{lane} --drive {drive} incident-triage {monitor['id']} --open \"<what is failing, for whom, the evidence>\"` for a live user-facing failure no lane covers; `--join <lane id> \"<why it is the same failure>\"` for one an open lane covers; or `--drop \"<why it is not a live user-facing failure>\"`. Change nothing and post nothing; end with that line."
        )
        with (store.drive_dir(drive) / LOG).open("a") as out:
            subprocess.Popen(session.argv(store.config(drive), "auditor", store.drive_dir(drive), {}, ["-p"], ["--permission-mode", "bypassPermissions", "--", prompt]), cwd=store.drive_dir(drive), stdin=subprocess.DEVNULL, stdout=out, stderr=out, start_new_session=True)
        started.append(f"triage of monitor {monitor['id']} ({monitor['name']})")
    return started


def triage(drive: str, monitor_id: str, verdict: str, why: str, lane_id: str | None = None) -> str:
    """The triage's verdict on one alert: open an incident lane with a diagnosis item, paging him only now, for a triaged live user-facing failure while he is away; join an open lane; or drop it, one logged line saying why. What it did."""
    from lane import verbs, work

    found = next((monitor for monitor in alerting(drive) if str(monitor["id"]) == monitor_id), None) or {"id": monitor_id, "name": (triaged(drive).get(monitor_id) or {}).get("name") or f"monitor {monitor_id}", "status": "Alert", "tags": []}
    mark(drive, monitor_id, {"verdict": verdict, "why": why, "lane": lane_id, "at": store.stamp()})
    with (store.drive_dir(drive) / LOG).open("a") as out:
        out.write(f"{store.stamp()} monitor {monitor_id} ({found['name']}): {verdict}{' ' + lane_id if lane_id else ''}: {why}\n")
    if verdict == "drop":
        return f"dropped monitor {monitor_id}: {why}"
    if verdict == "join":
        if lane_id not in open_incidents(drive):
            raise store.Refused(f"{lane_id} is no open incident lane: {', '.join(open_incidents(drive)) or 'none is open'}")
        joined(drive, lane_id, found)
        return f"monitor {monitor_id} joined {lane_id}"
    shape, ruled = store.config(drive), settings(drive)
    lane_id = f"incident-{monitor_id}"
    link = f"https://app.datadoghq.com/monitors/{monitor_id}"
    services = sorted(tag for tag in found.get("tags") or [] if tag.startswith("service:"))
    words = f"Datadog monitor {monitor_id} is {found['status']}: {found['name']}. {link}. Triage found: {why}. Start the fix now, narrate it in this lane's channel as ai-oncall does, and tell the person who reported it once the fix is live."
    verbs.open_lane(drive, lane_id, found["name"][:80], words=words, channel=ruled.get("channel"), worktree=shape.get("incident_worktree") or shape["checkout"], incident={"monitor": monitor_id, "link": link, "opened": store.stamp(), "services": services, "triage": why})
    work.add(drive, "ask", lane_id, f"diagnose monitor {monitor_id}", f"Diagnose why Datadog monitor {monitor_id} ({found['name']}, {link}) alerts; triage found: {why}. Read its query, the logs, traces and metrics behind it with the Datadog tools, and the recent deploys and merges, and finish this item with the mechanism and the evidence for it, so the lane's owner can aim the fix. Change nothing.", by="incident triage")
    if away(drive):
        page(drive, lane_id, f"{found['name']} ({link}): {why}; a fix lane and a diagnosis item started on it")
    return f"opened {lane_id}"
