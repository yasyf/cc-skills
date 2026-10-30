import datetime as dt
import re
from pathlib import Path
from zoneinfo import ZoneInfo

PACIFIC = ZoneInfo("America/Los_Angeles")
PR = re.compile(r"#(\d+)")
DONE = re.compile(r"\b(LIVE|passed|published)\b", re.I)
STARTED = re.compile(r"\bstarted\b", re.I)
FAILED = re.compile(r"\bfailed\b", re.I)
PROMISED = re.compile(r"\bby \d|\d:\d\d|\b\d{1,2} ?(AM|PM)\b", re.I)
OPEN_KINDS = {
    "queued": "in the merge queue",
    "waiting": "waiting on a review or a tap",
    "doing": "being built",
    "failing": "with a failing build",
    "you": "waiting on your tap",
}
NEXT_BY_KIND = {
    "queued": "the merge queue lands the queued ones",
    "waiting": "the graders and the board take the waiting ones",
    "doing": "their workers finish the ones being built",
    "failing": "their workers fix the failing builds",
    "you": "you tap the ones on your board",
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


def timed(step, by, since):
    if by:
        return f"{step}, {promised(by)}"
    if PROMISED.search(step):
        return step
    return f"{step}, no time given" + (f" since {clock(since)}" if since else "")


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
            "seq": record["seq"],
        })
    return found


def compute(config_milestones, drive_prs, blockers, releases):
    states = {entry["number"]: entry["state"] for entry in drive_prs}
    result = []
    for index, milestone in enumerate(config_milestones):
        release = milestone.get("release")
        row = {"name": milestone["name"], "release": release, "waits": "", "unblocking": "", "since": None, "owners": [], "blockers": []}
        found = blockers_of(blockers, release)
        shipped, shipping = release_events(releases, milestone.get("live_match"))
        tested, testing = release_events(releases, milestone.get("testing_match"))
        changes = changes_of(milestone)
        still_open = [n for n in changes if states.get(n, "landed") != "landed"]
        before = result[index - 1] if index else None
        if found:
            first = min(found, key=lambda b: b["since"])
            unknown = [b for b in found if not b["next"]]
            row.update(
                state="broken",
                label="Blocked",
                waits=first["text"] if len(found) == 1 else f"{first['text']} (and {len(found) - 1} more)",
                unblocking="; ".join(timed(b["next"], b["by"], b["since"]) for b in found if b["next"]) + (f"; {len(unknown)} without a next step yet, asked of their owners" if unknown else ""),
                since=first["since"],
                owners=sorted({b["owner"] for b in found}),
                blockers=found,
            )
        elif shipped and not still_open:
            row.update(state="live", label="Live", since=shipped["at"])
        elif shipping:
            row.update(state="progress", label="Releasing", waits=f"Its release started at {clock(shipping['at'])}", unblocking="The release worker follows it to live", since=shipping["at"], owners=[shipping["lane"]])
        elif still_open:
            counts = {}
            for n in still_open:
                counts[states[n]] = counts.get(states[n], 0) + 1
            row.update(
                state="progress",
                label="Being built",
                waits=f"{len(still_open)} of {len(changes)} changes still open: " + ", ".join(f"{count} {OPEN_KINDS.get(kind, 'in progress')}" for kind, count in counts.items()),
                unblocking="; ".join(NEXT_BY_KIND.get(kind, "their workers move them") for kind in counts).capitalize(),
                owners=[milestone["owner"]] if "owner" in milestone else [],
            )
        elif testing:
            row.update(state="progress", label="Publishing a test build", waits=f"A test build started at {clock(testing['at'])}", unblocking="The release worker follows it to the test channel", since=testing["at"], owners=[testing["lane"]])
        elif tested:
            row.update(state="progress", label="Testing", waits="Its build is out to testers with the switch off", unblocking="The testers check it; problems they find show here as blockers", since=tested["at"], owners=[tested["lane"]])
        elif before and before["state"] != "live":
            row.update(state="not", label="Not started", waits=f"{before['name']} first", unblocking=f"It starts once {before['name']} is live")
        elif changes or milestone.get("live_match"):
            row.update(state="next", label="Ready to release", waits="Its release has not started", unblocking=timed("The release worker starts it", None, None), owners=[milestone["owner"]] if "owner" in milestone else [])
        else:
            row.update(state="not", label="Not started", waits="Nothing tracks it yet", unblocking="The coordinator names its changes")
        result.append(row)
    return result
