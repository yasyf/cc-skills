"""His dashboard's queue as the lane drive holds it: each stack ready for his tap by lane-land's rule, lane by lane in the priority queue's order, a lane whose incident hurts users first, for the old dashboard to draw in its own item shape; what is not ready yet stays on his lane page's counts, so the queue is never a wall of every unapproved PR."""

import re
from datetime import datetime

from lane import owner as owners
from lane import page, rank, store
from lane.rows import CHECKS

STAMPED = re.compile(r"^(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ) (.*)$")
MERGE_QUEUED = re.compile(r"queued for merge: ([\w.-]+/[\w.-]+#\d+)")
HISTORY_LINES = 400


def taps(drive: str) -> list[dict] | None:
    """Each stack ready for his tap, in queue order: its bottom PR at the head GitHub read last, the PRs stacked above it, its lane, its position, and whether its lane's incident hurts users; None when the drive runs no live lanes, so the old drive's queue stands."""
    if not store.live(drive):
        return None
    lanes = store.load_all(drive)
    ranked = rank.last(drive)
    hurting = set(ranked.get("hurting", []))
    order = [lane_id for lane_id in ranked.get("order", []) if lane_id in lanes] + sorted(set(lanes) - set(ranked.get("order", [])))
    order.sort(key=lambda lane_id: lane_id not in hurting)
    seen = page.view(drive)["prs"]
    found = []
    for position, lane_id in enumerate(order, 1):
        stacks, _ = page.ready(page.tapless(lanes[lane_id], seen), seen)
        for row in stacks:
            key = f"{row['repo']}#{row['number']}"
            found.append({
                "key": key,
                "head": seen[key]["head"],
                "checks": CHECKS.get(row["checks"] or "", "pending"),
                "title": row.get("words") or seen[key].get("title") or key,
                "moved": row["moved"],
                "stack": [{"key": member, "url": url(member), "head": seen[member]["head"], "title": seen[member].get("title") or member, "checks": CHECKS.get(seen[member]["rollup"] or "", "pending")} for member in [key, *row["above"]]],
                "lane": lane_id,
                "lane_title": lanes[lane_id]["title"],
                "position": position,
                "hurting": lane_id in hurting,
            })
    return found


def url(key: str) -> str:
    repo, _, number = key.rpartition("#")
    return f"https://github.com/{repo}/pull/{number}"


def waits_plainly(wait: dict) -> str:
    """What a waiting lane waits on, in his words: the outside facts it names, else only its time bound or his next message."""
    said = {
        "pr_merged": lambda ref: f"{short(ref)} to merge",
        "check": lambda ref: f"checks on {short(ref)}",
        "staging_result": lambda ref: "its staging test",
        "release": lambda ref: f"release {ref}",
        "queue_grant": lambda ref: f"its turn for the {ref.replace('-', ' ')}",
    }
    facts = [said[event["kind"]](event["ref"]) for event in wait.get("on", []) if event["kind"] in said]
    return ", ".join(facts) or "your next message"


def short(ref: str) -> str:
    return f"#{ref.rpartition('#')[2]}"


def entries(drive: str, lane_id: str) -> list[dict]:
    """The lane's log as entries, each stamped line with the unstamped lines under it, the last HISTORY_LINES lines only."""
    path = store.log_path(drive, lane_id)
    lines = path.read_text().splitlines()[-HISTORY_LINES:] if path.is_file() else []
    found: list[dict] = []
    for line in lines:
        if stamped := STAMPED.match(line):
            found.append({"at": stamped[1], "words": stamped[2]})
        elif found:
            found[-1]["words"] += "\n" + line
    return found


def screens(drive: str) -> dict | None:
    """His Now, Work and History screens on a live lane drive, None on any other: Now, the lanes in queue order with what it waits on in his words, its next step, its wait, his unanswered asks, its ready stacks and what else waits on him counted by why; Work, each running owner with what its job says it does and how many builders it runs; History, each lane's log and each lane PR GitHub reads merged, at the time its lane queued it for merge."""
    if not store.live(drive):
        return None
    lanes = store.load_all(drive)
    ranked = rank.last(drive)
    hurting = set(ranked.get("hurting", []))
    order = [lane_id for lane_id in ranked.get("order", []) if lane_id in lanes] + sorted(set(lanes) - set(ranked.get("order", [])))
    order.sort(key=lambda lane_id: lane_id not in hurting)
    seen = page.view(drive)["prs"]
    now, work, history = [], [], []
    for position, lane_id in enumerate(order, 1):
        lane = lanes[lane_id]
        stacks, rest = page.ready(page.tapless(lane, seen), seen)
        now.append({
            "id": lane_id, "title": lane["title"], "position": position, "state": lane["state"], "hurting": lane_id in hurting,
            "waits": waits_plainly(lane["wait"]) if lane["state"] == store.WAITING else None, "parked": (lane.get("parked") or {}).get("words"), "next": lane.get("next"), "until": (lane.get("wait") or {}).get("until"), "why": (lane.get("wait") or {}).get("why"),
            "asks": [{"words": ask["words"], "link": ask.get("link"), "at": ask["at"]} for ask in store.unreplied(lane)],
            "stacks": [{"key": f"{row['repo']}#{row['number']}", "url": url(f"{row['repo']}#{row['number']}"), "title": row.get("words") or seen[f"{row['repo']}#{row['number']}"].get("title") or "", "above": len(row["above"])} for row in stacks],
            "rest": rest, "channel": lane.get("channel"), "thread": lane.get("thread"),
        })
        if lane["state"] == store.RUNNING:
            owner = lane["owner"]
            job = owners.record(owners.config_dir() / "jobs" / owner["job"] / "state.json") if owner.get("job") else None
            work.append({"id": lane_id, "title": lane["title"], "since": owner.get("launched"), "doing": (job or {}).get("detail") or lane.get("next") or "", "builders": owners.in_flight(owner), "updated": (job or {}).get("updatedAt")})
        logged = entries(drive, lane_id)
        queued = {found[1]: entry["at"] for entry in logged if (found := MERGE_QUEUED.search(entry["words"]))}
        history += [{"lane": lane_id, "title": lane["title"], "kind": "said", **entry} for entry in logged]
        history += [{"lane": lane_id, "title": lane["title"], "kind": "merged", "at": queued[key], "words": f"{short(key)} {seen[key].get('title') or ''}".strip(), "url": url(key)} for row in lane["prs"] if (key := f"{row['repo']}#{row['number']}") in queued and (seen.get(key) or {}).get("merged")]
    history.sort(key=lambda entry: entry["at"], reverse=True)
    today = datetime.now().astimezone().date()
    return {"now": now, "work": work, "history": history, "merged_today": sum(entry["kind"] == "merged" and store.parse(entry["at"]).astimezone().date() == today for entry in history)}
