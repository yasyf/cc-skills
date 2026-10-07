from __future__ import annotations

import re
import statistics
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from livedash import view

PACIFIC = ZoneInfo("America/Los_Angeles")
RELEASE_KINDS = frozenset({"release", "hotfix", "rollback", "deploy", "plan", "check", "dry-run"})
APPLYING = frozenset({"release", "hotfix", "rollback", "deploy"})
FINISHED = frozenset({"passed", "failed", "canceled", "cancelled"})
LIVE_STATES = frozenset({"running", "scheduled", "creating", "blocked", "canceling", "failing"})
VERBS = {"release": "Release", "hotfix": "Hotfix", "rollback": "Roll back", "deploy": "Deploy", "plan": "Plan", "dry-run": "Dry run of", "check": "Release check"}
RELEASE_LIMIT = 200
DURATION_BARS = 30
LANDED_HOURS = 24
CENSUS_POINTS = 48
LIVE_LANE = timedelta(minutes=30)
LIVE_ITEMS = 8
QUIET_INCIDENT = timedelta(hours=24)
SUBJECT_PR = re.compile(r"\s*\(#(?P<pr>\d+)\)\s*$")
SLACK_URL = re.compile(r"https://[\w-]+\.slack\.com/archives/\w+/p\d+")
ROLE = r"-(?:fix|evidence|diag|diagnosis|mechanism)(?:-(?:opus|sol|r?\d+))?"
LANE_ROLE = re.compile(f"{ROLE}$")
CLOCK_TAIL = re.compile(r"-\d{4}$")
SIGHTED = re.compile(r"\b(?P<h>1[0-2]|0?[1-9]):(?P<m>[0-5]\d)(?:\s*-\s*(?:1[0-2]|0?[1-9]):[0-5]\d)?\s*(?P<ampm>[AP]M)\b")
INCIDENT_KINDS = ("incident", "evidence", "mechanism", "fix-live", "recovered", "not-ours", "duplicate", "done")
OPENING = frozenset({"incident", "evidence", "mechanism", "fix-live", "recovered"})
NAMING = frozenset({"fix-live", "recovered", "not-ours", "duplicate"})
STATUS = {"incident": "open", "evidence": "investigating", "mechanism": "mechanism found", "fix-live": "fix live", "recovered": "recovered", "not-ours": "not ours", "duplicate": "duplicate", "done": "done"}
SETTLED = frozenset({"fix live", "recovered", "not ours", "duplicate", "done"})
CURATED = "resolved:"
CITE_SEPARATOR = ","
DIGEST_OPEN = ("open_asks", "open_holds", "open_incidents", "open_blockers", "open_defects", "untracked_holds")
LIFTING = frozenset({"lift", "go", "decision", "owner", "answer"})
LIFT_LEAD = re.compile(r"^(?:ROOT(?: [\dx:]+ [AP]M)?:\s*)?LIFT(?:ED|S)?\b")
SLUG = re.compile(r"(?<![\w-])[a-z][a-z0-9]*(?:-[a-z0-9]+)*-\d{4}(?![\w-])")
MONITOR = re.compile(r"(?<!\d)\d{9}(?!\d)")
HEADLINE_LEAD = re.compile(r"^(?:(?:(?:INCIDENT|RECOVERED|NEW Alert \d+|NEW|ROOT)\b|R\?)\s*|[\d:x-]+\s*[AP]M(?:\s*PT)?\b\s*|#[\w-]+\s*|[,:—-]\s*)+")
HEADLINE_CHARS = 90
URL = re.compile(r"\s*https?://\S+")


def iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def person(email: str | None) -> str | None:
    if not email:
        return None
    return " ".join(part.capitalize() for part in re.split(r"[._-]", email.split("@")[0]) if part)


def listed(items: list[str]) -> str:
    return items[0] if len(items) == 1 else f"{', '.join(items[:-1])} and {items[-1]}"


def stacks_phrase(stacks: list[str]) -> str:
    components: dict[str, list[str]] = {}
    for stack in stacks:
        component, _, env = stack.partition("/")
        components.setdefault(component, []).append(env)
    envs = sorted({env for found in components.values() for env in found})
    if len(components) == 1:
        component, found = next(iter(components.items()))
        return f"{component} to {found[0]}" if len(found) == 1 else f"{component} to {len(found)} environments"
    if len(components) <= 3:
        return f"{listed(list(components))} to {envs[0]}" if len(envs) == 1 else listed(list(components))
    return f"{len(stacks)} stacks across {len(components)} components"


def title_of(build: dict) -> str:
    verb = VERBS[build["kind"]]
    if build["kind"] == "check":
        return verb
    if build["stacks"]:
        return f"{verb} {stacks_phrase(build['stacks'])}"
    return f"{verb} {listed(build['targets'])}"


def minutes_between(start: str | None, end: str | None) -> float | None:
    if not (start and end):
        return None
    return round((view.stamp(end) - view.stamp(start)).total_seconds() / 60, 1)


def slack_links(lines: list[dict], pipeline: str) -> dict[int, str]:
    build_url = re.compile(rf"https://buildkite\.com/[\w-]+/{re.escape(pipeline)}/builds/(?P<number>\d+)")
    links: dict[int, str] = {}
    for line in lines:
        builds = {int(match["number"]) for match in build_url.finditer(line["text"])}
        if len(builds) == 1 and (slack := SLACK_URL.search(line["text"])):
            links.setdefault(builds.pop(), slack[0])
    return links


def thread_url(thread: dict | None, workspace: str | None) -> str | None:
    if not (thread and workspace):
        return None
    return f"{workspace.rstrip('/')}/archives/{thread['channel']}/p{thread['ts'].replace('.', '')}"


def launchers(builds: list[dict]) -> dict[int, int]:
    started: dict[int, int] = {}
    pending: dict[str, list[int]] = {}
    for build in sorted(builds, key=lambda build: build["number"]):
        if build["kind"] not in RELEASE_KINDS:
            continue
        if not build.get("applies") and build["state"] == "passed":
            pending.setdefault(build["message"], []).append(build["number"])
        elif build.get("applies") and (queue := pending.get(build["message"])):
            tail = build["branch"].rsplit("/", 1)[-1]
            launcher = int(tail) if tail.isdigit() and int(tail) in queue else queue[0]
            queue.remove(launcher)
            started[launcher] = build["number"]
    return started


def release_rows(builds: list[dict], subjects: dict[str, str], repo: str, workspace: str | None, linked: dict[int, str], moment: datetime) -> list[dict]:
    folded = launchers(builds)
    launched = {applied: launcher for launcher, applied in folded.items()}
    rows = []
    for build in builds:
        if build["kind"] not in RELEASE_KINDS or build["number"] in folded:
            continue
        subject = subjects.get(build["commit"]) or ""
        found = SUBJECT_PR.search(subject)
        live = build["state"] in LIVE_STATES
        rows.append(
            {
                "number": build["number"],
                "kind": build["kind"],
                "state": build["state"],
                "title": title_of(build),
                "by": person(build.get("starter")),
                "at": build["at"],
                "started_at": build.get("started_at"),
                "finished_at": build.get("finished_at"),
                "minutes": minutes_between(build.get("started_at") or build["at"], build.get("finished_at") or (iso(moment) if live else None)),
                "live": live,
                "applies": bool(build.get("applies")),
                "url": build["url"],
                "slack": thread_url(build.get("thread"), workspace) or linked.get(build["number"]) or linked.get(launched.get(build["number"], 0)),
                "launcher": launched.get(build["number"]),
                "commit": build["commit"],
                "pr": int(found["pr"]) if found else None,
                "pr_title": SUBJECT_PR.sub("", subject) or None,
                "pr_url": f"https://github.com/{repo}/pull/{found['pr']}" if found else None,
                "stacks": build["stacks"],
                "targets": build["targets"],
                "without": build.get("without") or [],
                "deselected": build.get("deselected") or [],
                "destructive": bool(build.get("destructive")),
                "steps": build.get("steps"),
                "now": build.get("now") or [],
                "failed_steps": build.get("failed_steps") or [],
                "message": build["message"],
                "cite": f"build:{build['number']}",
            }
        )
        if len(rows) == RELEASE_LIMIT:
            break
    return rows


def pacific_day(stamp: str | None) -> str | None:
    return view.stamp(stamp).astimezone(PACIFIC).date().isoformat() if stamp else None


def incident_key(record: dict, known: set[str]) -> str:
    if record.get("topic"):
        return record["topic"]
    lane = record["lane"]
    base = LANE_ROLE.sub("", lane)
    if lane in known or base in known:
        return lane if lane in known else base
    if mentioned := [slug for slug in known if names(slug, record["text"])]:
        return max(mentioned, key=len)
    if (clock := CLOCK_TAIL.search(lane)) and len(matches := [slug for slug in known if slug.endswith(clock[0])]) == 1:
        return matches[0]
    if (sighted := SIGHTED.search(record["text"])) and len(matches := [slug for slug in known if slug.endswith(f"-{int(sighted['h']) % 12 + (12 if sighted['ampm'] == 'PM' else 0):02d}{sighted['m']}")]) == 1:
        return matches[0]
    return f"seq:{record['seq']}"


def headline(text: str) -> str:
    line = URL.sub("", HEADLINE_LEAD.sub("", text.split("\n", 1)[0])).strip()
    return line if len(line) <= HEADLINE_CHARS else line[: HEADLINE_CHARS - 1].rsplit(" ", 1)[0] + "…"


def keyed(records: list[dict], known: set[str]) -> dict[int, str]:
    keys = {record["seq"]: incident_key(record, known) for record in records}
    owner: dict[str, str] = {}
    for record in sorted(records, key=lambda record: record["seq"]):
        monitors = MONITOR.findall(record["text"])
        claimed = next((owner[monitor] for monitor in monitors if monitor in owner), None)
        if claimed and keys[record["seq"]].startswith("seq:"):
            keys[record["seq"]] = claimed
        for monitor in monitors:
            owner.setdefault(monitor, keys[record["seq"]])
    return keys


def curated(records: list[dict]) -> set[str]:
    return {cite for record in records if (record.get("topic") or "").startswith(CURATED) for cite in record["topic"].removeprefix(CURATED).split(CITE_SEPARATOR) if cite}


def uncurated(digest: dict, closed: set[str]) -> dict:
    return {key: [record for record in value or [] if f"cci:{record['seq']}" not in closed] if key in DIGEST_OPEN else value for key, value in digest.items()}


def names(slug: str, text: str) -> bool:
    return bool(re.search(rf"(?<![\w-]){re.escape(slug)}(?:{ROLE})?(?![\w-])", text))


def named_groups(record: dict, keys: set[str]) -> list[str]:
    return [key for key in sorted(keys) if not key.startswith("seq:") and names(key, record["text"])]


def incident_groups(records: list[dict], open_seqs: set[int], folders: list[dict], moment: datetime, closed: set[str], resolved: set[int]) -> list[dict]:
    files = {folder["slug"]: folder for folder in folders}
    keyed_records = [record for record in records if record["kind"] != "done"]
    known = set(files) | {record["topic"] for record in keyed_records if record.get("topic")} | {base for record in keyed_records if (base := LANE_ROLE.sub("", record["lane"])) != record["lane"] and record["lane"].endswith(("-fix", "-fix-opus", "-fix-sol"))}
    keys = keyed(keyed_records, known)
    groups: dict[str, list[dict]] = {}
    for record in keyed_records:
        if record["kind"] in OPENING and (record["kind"] == "incident" or not keys[record["seq"]].startswith("seq:")):
            groups.setdefault(keys[record["seq"]], []).append(record)
    opened = set(groups)
    for record in keyed_records:
        if record["kind"] in NAMING:
            for key in {keys[record["seq"]], *named_groups(record, opened)} & opened:
                if record not in groups[key]:
                    groups[key].append(record)
    home = {record["seq"]: key for key, found in groups.items() for record in found}
    for record in records:
        if record["kind"] == "done" and (key := home.get(record.get("re")) or home.get(record.get("resolves"))):
            groups[key].append(record)
    out = []
    for key, found in groups.items():
        found.sort(key=lambda record: record["seq"])
        latest = found[-1]
        status = STATUS[latest["kind"]]
        reported = [record for record in found if record["kind"] == "incident"]
        still_open = any(record["seq"] in open_seqs for record in reported)
        if status not in SETTLED and ((reported and not still_open) or f"incident:{key}" in closed or any(record["seq"] in resolved for record in found)):
            status = "resolved"
        quiet = moment - view.stamp(latest["at"]) > QUIET_INCIDENT
        opener = reported[0] if reported else found[0]
        sighting = key.startswith("seq:")
        out.append(
            {
                "key": key,
                "title": headline(opener["text"]) if sighting else key,
                "sighting": sighting,
                "status": status,
                "active": status not in SETTLED | {"resolved"} and not quiet and (not sighting or still_open),
                "quiet": quiet,
                "at": latest["at"],
                "opened_at": opener["at"],
                "summary": opener["text"],
                "latest": headline(latest["text"]),
                "lanes": sorted({record["lane"] for record in found}),
                "records": [{"seq": record["seq"], "kind": record["kind"], "lane": record["lane"], "at": record["at"], "text": record["text"]} for record in reversed(found)],
                "folder": files.get(key, {}).get("path"),
                "files": files.get(key, {}).get("text"),
                "cite": f"incident:{key}",
            }
        )
    return sorted(out, key=lambda group: (group["active"], group["at"]), reverse=True)


def lifts(lift: dict, hold: dict) -> bool:
    if lift["seq"] <= hold["seq"]:
        return False
    if re.search(rf"#{hold['seq']}(?!\d)", lift["text"]):
        return True
    return lift["lane"] in (hold["lane"], "root", "owner") and bool(set(SLUG.findall(hold["text"])) & set(SLUG.findall(lift["text"])))


def standing_holds(holds: list[dict], records: list[dict]) -> list[dict]:
    lifting = [record for record in records if record["kind"] in LIFTING and LIFT_LEAD.match(record["text"])]
    return [hold for hold in holds if not any(lifts(lift, hold) for lift in lifting)]


def landings(records: list[dict], titles: dict[int, str], repo: str) -> list[dict]:
    seen: dict[int, dict] = {}
    for record in sorted(records, key=lambda record: record["seq"]):
        for pr in record["refs"].get("prs") or []:
            seen.setdefault(pr, {"pr": pr, "at": record["at"], "lane": record["lane"], "title": titles.get(pr) or record["text"], "url": f"https://github.com/{repo}/pull/{pr}"})
    return sorted(seen.values(), key=lambda row: row["at"], reverse=True)


def landed_hours(landed: list[dict], moment: datetime) -> list[dict]:
    top = moment.astimezone(timezone.utc).replace(minute=0, second=0, microsecond=0)
    hours = [top - timedelta(hours=offset) for offset in reversed(range(LANDED_HOURS))]
    counts: dict[datetime, list[dict]] = {hour: [] for hour in hours}
    for row in landed:
        hour = view.stamp(row["at"]).astimezone(timezone.utc).replace(minute=0, second=0, microsecond=0)
        if hour in counts:
            counts[hour].append(row)
    return [{"hour": iso(hour), "count": len(prs), "prs": prs} for hour, prs in counts.items()]


def census_trend(records: list[dict], current: dict | None, moment: datetime) -> list[dict]:
    points = [{"at": record["at"], "n": record["fields"]["census"]["n"], "total": record["fields"]["census"]["denominator"]} for record in records if (record.get("fields") or {}).get("census")]
    if current:
        points.append({"at": iso(moment), "n": current["at_zero"], "total": current["total"]})
    return points[-CENSUS_POINTS:]


def census_states(stacks: list[dict]) -> list[dict]:
    order = ("0/0", "drift", "unplanned")
    return [{"state": state, "count": sum(row["zero"] == state for row in stacks)} for state in order]


def live_lanes(lanes: list[dict], moment: datetime) -> list[dict]:
    return [lane for lane in lanes if lane["lane"] != "owner" and moment - view.stamp(lane["at"]) <= LIVE_LANE]


def live_items(releases: list[dict], lanes: list[dict], incidents: list[dict], holds: list[dict]) -> list[dict]:
    items = [
        {"kind": "release", "at": row["started_at"] or row["at"], "title": f"#{row['number']} {row['title']}", "detail": "; ".join(row["now"][:2]) or row["state"], "state": row["state"], "steps": row["steps"], "url": row["url"], "ref": row["number"]}
        for row in releases
        if row["live"]
    ]
    items += [{"kind": "incident", "at": group["at"], "title": group["title"], "detail": group["latest"], "state": group["status"], "ref": group["key"]} for group in incidents if group["active"]]
    items += [{"kind": "lane", "at": lane["at"], "title": lane["lane"], "detail": lane["text"], "state": lane["kind"], "ref": lane["lane"]} for lane in lanes]
    items += [{"kind": "hold", "at": hold["at"], "title": f"Hold by {hold['lane']}", "detail": hold["text"], "state": "hold", "ref": hold["seq"]} for hold in holds]
    return sorted(items, key=lambda item: item["at"] or "", reverse=True)


def overview(releases: list[dict], stacks: dict | None, platy_rows: list[dict], open_prs: int, landed: list[dict], lanes: list[dict], incidents: list[dict], holds: list[dict], census: list[dict], moment: datetime) -> dict:
    today = moment.astimezone(PACIFIC).date().isoformat()
    applying = [row for row in releases if row["kind"] in APPLYING and row["applies"]]
    finished = [row for row in applying if row["state"] in FINISHED and row["minutes"] is not None]
    passed = [row["minutes"] for row in finished[:DURATION_BARS] if row["state"] == "passed"]
    todays = [row for row in applying if pacific_day(row["at"]) == today]
    working = live_lanes(lanes, moment)
    return {
        "tiles": {
            "stacks": stacks,
            "releases_today": {state: sum(row["state"] == state for row in todays) for state in ("passed", "failed", "canceled")} | {"running": sum(row["live"] for row in todays)},
            "median_minutes": round(statistics.median(passed), 1) if passed else None,
            "median_of": len(passed),
            "prs": {"open": open_prs, "landed_today": sum(pacific_day(row["at"]) == today for row in landed)},
            "incidents": sum(group["active"] for group in incidents),
            "sightings": sum(group["active"] and group["sighting"] for group in incidents),
            "lanes": len(working),
        },
        "charts": {
            "durations": [{"number": row["number"], "state": row["state"], "minutes": row["minutes"], "title": row["title"], "at": row["at"]} for row in reversed(finished[:DURATION_BARS])],
            "census": census_trend(census, stacks, moment),
            "landed": landed_hours(landed, moment),
            "states": census_states(platy_rows),
        },
        "live": live_items(releases, working, incidents, holds)[:LIVE_ITEMS * 4],
        "last_release": next((row for row in applying if row["state"] in FINISHED), None),
    }
