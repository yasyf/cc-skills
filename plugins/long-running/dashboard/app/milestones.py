import datetime as dt
import re
from pathlib import Path
from zoneinfo import ZoneInfo

PACIFIC = ZoneInfo("America/Los_Angeles")
PR = re.compile(r"#(\d+)")
DONE = re.compile(r"\b(LIVE|passed|published)\b", re.I)
STARTED = re.compile(r"\bstarted\b", re.I)
FAILED = re.compile(r"\bfailed\b", re.I)
OPEN_KINDS = {
    "queued": "queued",
    "waiting": "in review",
    "doing": "being built",
    "failing": "failing build",
    "you": "on your board",
}
NEXT_BY_KIND = {
    "you": "You: tap the ones on your board",
    "failing": "Their workers: fix the failing builds",
    "waiting": "Graders and the board: take the waiting ones",
    "doing": "Their workers: finish the ones being built",
    "queued": "Merge queue: lands the queued ones",
}


def when(iso):
    return dt.datetime.fromisoformat(iso.replace("Z", "+00:00"))


def clock(iso):
    at = when(iso).astimezone(PACIFIC)
    label = at.strftime("%-I:%M %p")
    return label if at.date() == dt.datetime.now(PACIFIC).date() else f"{label} {at:%b} {at.day}"


def promised(by):
    try:
        return f"by {clock(by)}"
    except ValueError:
        return by


def timed(step, by):
    return f"{step}, {promised(by)}" if by else step


def changes_of(milestone):
    if "changes" in milestone:
        return [int(n) for n in milestone["changes"]]
    if "changes_file" in milestone:
        return [int(n) for n in re.findall(r"^#(\d+)", Path(milestone["changes_file"]).expanduser().read_text(), re.M)]
    return []


def release_events(releases, pattern):
    if not pattern:
        return None, None
    matching = [r for r in releases if re.search(pattern, r["text"])]
    done = next((r for r in matching if DONE.search(r["text"])), None)
    newest = matching[-1] if matching else None
    running = newest if newest and STARTED.search(newest["text"]) and not DONE.search(newest["text"]) and not FAILED.search(newest["text"]) else None
    return done, running


def blockers_of(blockers, release):
    newest = {}
    for record in blockers:
        attrs = (record.get("fields") or {}).get("attrs") or {}
        if attrs.get("milestone") == release:
            newest[attrs.get("topic", "")] = record
    found = []
    for record in newest.values():
        attrs = record["fields"]["attrs"]
        if attrs.get("cleared"):
            continue
        found.append({
            "text": record["text"],
            "owner": attrs.get("owner") or (record.get("to") or [record["lane"]])[0],
            "next": attrs.get("next") or "",
            "since": attrs.get("since") or record["at"],
            "by": attrs.get("by"),
            "why": attrs.get("why") or "",
            "changed": attrs.get("changed") or "",
            "at": record["at"],
            "seq": record["seq"],
        })
    return found


def compute(config_milestones, drive_prs, blockers, releases):
    states = {entry["number"]: entry["state"] for entry in drive_prs}
    result = []
    for index, milestone in enumerate(config_milestones):
        release = milestone.get("release")
        row = {"name": milestone["name"], "release": release, "waits": "", "next": "", "since": None, "changed": None, "why": "", "owners": [], "blockers": []}
        found = blockers_of(blockers, release)
        shipped, shipping = release_events(releases, milestone.get("live_match"))
        tested, testing = release_events(releases, milestone.get("testing_match"))
        changes = changes_of(milestone)
        still_open = [n for n in changes if states.get(n, "landed") != "landed"]
        before = result[index - 1] if index else None
        if found:
            first = min(found, key=lambda b: b["since"])
            latest = max(found, key=lambda b: b["at"])
            acting = next((b for b in sorted(found, key=lambda b: b["since"]) if b["next"]), None)
            row.update(
                state="broken",
                label="Blocked",
                waits=first["text"] if len(found) == 1 else f"{first['text']} (+{len(found) - 1} more)",
                next=timed(acting["next"], acting["by"]) if acting else "Coordinator: names the next step",
                since=first["since"],
                changed={"at": latest["at"], "text": latest["changed"]} if latest["changed"] else None,
                why=first["why"],
                owners=sorted({b["owner"] for b in found}),
                blockers=found,
            )
        elif shipped and not still_open:
            row.update(state="live", label="Live", since=shipped["at"])
        elif shipping:
            row.update(state="progress", label="Releasing", next="Release worker: follows it to live", since=shipping["at"], changed={"at": shipping["at"], "text": "release started"}, owners=[shipping["lane"]])
        elif still_open:
            counts = {}
            for n in still_open:
                counts[states[n]] = counts.get(states[n], 0) + 1
            row.update(
                state="progress",
                label="Building",
                waits=f"{len(still_open)} of {len(changes)} changes open: " + ", ".join(f"{count} {OPEN_KINDS.get(kind, 'in progress')}" for kind, count in counts.items()),
                next=next((step for kind, step in NEXT_BY_KIND.items() if kind in counts), "Their workers: move them"),
                owners=[milestone["owner"]] if "owner" in milestone else [],
            )
        elif testing:
            row.update(state="progress", label="Publishing", next="Release worker: follows it to the test channel", since=testing["at"], changed={"at": testing["at"], "text": "test build started"}, owners=[testing["lane"]])
        elif tested:
            row.update(state="progress", label="Testing", waits="Testers, with the switch off", next="Testers: report problems here as blockers", since=tested["at"], changed={"at": tested["at"], "text": "test build published"}, owners=[tested["lane"]])
        elif before and before["state"] != "live":
            row.update(state="not", label="Later", waits=f"{before['name']} first")
        elif changes or milestone.get("live_match"):
            row.update(state="next", label="Ready", waits="Its release has not started", next="Release worker: starts it", owners=[milestone["owner"]] if "owner" in milestone else [])
        else:
            row.update(state="not", label="Untracked", waits="Nothing tracks it yet", next="Coordinator: names its changes")
        result.append(row)
    return result
