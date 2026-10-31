"""The drive's plan in plain words: one row per plan step and per milestone, and what is left until completion.

It reads the progress desk's ``steps.json`` alone, so it answers in milliseconds with no server, bus, or GitHub call.
A step's state counts its changes from the desk's live ``pr_state``; every other cell is the desk's own plain words,
with change numbers folded into "a change" and lane names into "a worker". ``--since`` keeps the rows that moved
after a time and says what moved. Stdlib only.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

STEPS = Path("progress-desk") / "steps.json"
PACIFIC = ZoneInfo("America/Los_Angeles")
PR_RUN = re.compile(r"#\d+(?:(?:,\s*|,?\s+(?:and|or)\s+)#\d+)*")
PR = re.compile(r"#\d+")
MILESTONE = re.compile(r"^R(\d+)$")
LANE_NAME = re.compile(r"[-\d]")
DURATION = re.compile(r"^(\d+)([mhd])$")
CLOCK = re.compile(r"^(\d{1,2}):(\d\d)\s*([AaPp][Mm])$")
UNITS = {"m": "minutes", "h": "hours", "d": "days"}
PLAIN_FOR = {
    "landing desk": "merge worker",
    "census box": "a temporary machine we delete afterwards",
    "pass box": "a temporary machine we delete afterwards",
    "lane": "worker",
    "desk": "standing worker",
    "watchdog": "the check that watches the workers",
    "finder": "automated code review",
    "slice": "the rules that apply here",
    "fidelity grade": "check against your rules",
    "read-back": "summary of your rules",
    "merge card": "merge question",
    "runner": "background script",
    "sweep": "search",
}
DRIVE_WORD = re.compile(r"\b(" + "|".join(re.escape(word) for word in sorted(PLAIN_FOR, key=len, reverse=True)) + r")(e?s)?\b", re.IGNORECASE)
HEADINGS = ("Step", "What it delivers", "State", "What blocks it", "What we are doing to unblock it", "What went wrong or slowed it so far")
MOVES = {
    "Landed": "landed",
    "New version, being graded": "got a new version",
    "Graded, waiting for a board row": "passed grading",
    "In the merge queue": "entered the merge queue",
    "On your board": "went on your board",
    "Approved by you": "got your approval",
    "Cleared to land": "cleared to land",
    "Refused by the queue": "refused by the merge queue",
    "Held": "put on hold",
    "Closed": "closed unmerged",
}


@dataclass(frozen=True)
class Row:
    id: str
    label: str
    name: str
    step: str
    delivers: str
    state: str
    blocker: str
    unblocking: str
    went_wrong: str
    done: bool
    moved: str = ""

    def cells(self) -> tuple[str, ...]:
        unblocking = self.unblocking or ("Nothing needed" if self.done else "Nobody is named for it yet")
        return tuple(capital(text) for text in (self.step, self.delivers, self.state, self.blocker or "Nothing", unblocking, self.went_wrong or "None recorded", self.moved))


def capital(text: str) -> str:
    return text[:1].upper() + text[1:]


def stated(category: str, detail: str) -> str:
    """``detail`` under the owner table's state word ``category``, unless it already opens with that word."""
    if not detail or detail.lower().startswith(category):
        return detail or category
    return f"{category}: {detail}"


def plain(text: str | None, lanes: list[str]) -> str:
    """``text`` with every run of change numbers folded into words and every lane name into "a worker"."""
    if not text:
        return ""
    folded = PR_RUN.sub(lambda run: "a change" if len(found := PR.findall(run[0])) == 1 else f"{len(found)} changes", text)
    for lane in sorted((lane for lane in lanes if LANE_NAME.search(lane)), key=len, reverse=True):
        folded = re.sub(rf"(?<![\w-]){re.escape(lane)}(?![\w-])", "a worker", folded)
    return DRIVE_WORD.sub(word, folded)


def word(found: re.Match) -> str:
    said = PLAIN_FOR[found[1].lower()] + ("s" if found[2] else "")
    return said[:1].upper() + said[1:] if found[1][:1].isupper() else said


def clock(moment: datetime, *, day: bool = True) -> str:
    local = moment.astimezone(PACIFIC)
    return f"{local:%-I:%M %p %b} {local.day}" if day else f"{local:%-I:%M %p}"


def instant(stamp: str) -> datetime:
    return datetime.fromisoformat(stamp)


def since(value: str, now: datetime) -> datetime:
    """The moment ``value`` names: ``90m``, ``2h`` or ``1d`` back from ``now``, a Pacific clock such as ``7:05 PM`` at its latest past occurrence, or an ISO time."""
    if found := DURATION.match(value):
        return now - timedelta(**{UNITS[found[2]]: int(found[1])})
    if found := CLOCK.match(value.strip()):
        hour = int(found[1]) % 12 + (12 if found[3].lower() == "pm" else 0)
        local = now.astimezone(PACIFIC).replace(hour=hour, minute=int(found[2]), second=0, microsecond=0)
        return local - timedelta(days=1) if local > now else local
    moment = instant(value)
    return moment if moment.tzinfo else moment.replace(tzinfo=PACIFIC)


def every_lane(doc: dict) -> list[str]:
    named = [lane for step in doc["steps"] for lane in step.get("lanes") or []]
    named += [lane for release in doc["now"].get("releases") or [] for lane in release.get("lanes") or []]
    named += [key.removeprefix("lane:") for key in doc.get("history") or {} if key.startswith("lane:")]
    return list(dict.fromkeys(named))


def moved(doc: dict, prs: list[str], lanes: list[str], after: datetime, names: list[str]) -> str:
    states = doc.get("pr_state") or {}
    counts: dict[str, int] = {}
    for number in prs:
        entry = states.get(number) or {}
        if entry.get("since") and entry["state"] in MOVES and instant(entry["since"]) >= after:
            counts[entry["state"]] = counts.get(entry["state"], 0) + 1
    said = [f"{'1 change' if n == 1 else f'{n} changes'} {MOVES[state]}" for state, n in counts.items()]
    history = doc.get("history") or {}
    events = [event for key in [*(f"pr:{n}" for n in prs), *(f"lane:{lane}" for lane in lanes)] for event in (history.get(key) or {}).get("events") or []]
    latest = max((event for event in events if instant(event["ts"]) >= after), key=lambda event: event["ts"], default=None)
    if latest:
        said.append(f"latest at {clock(instant(latest['ts']), day=False)}: {plain(latest['text'], names).rstrip('.')}")
    return "; ".join(said)


def step_row(order: int, step: dict, doc: dict, names: list[str], after: datetime | None) -> Row:
    states = doc.get("pr_state") or {}
    prs = [number for n in step.get("prs") or [] if (number := str(n)) in states and states[number]["state"] != "Closed"]
    still = [states[number] for number in prs if states[number]["state"] != "Landed"]
    done = not still if prs else step.get("tone") == "done"
    if prs:
        state = "landed" if done else stated("in progress", f"{len(prs) - len(still)} of {len(prs)} changes landed")
    else:
        detail = plain(step.get("state"), names)
        state = stated("done" if done else "not started" if step.get("tone") == "later" or detail.lower().startswith("not started") else "in progress", detail)
    name = plain(step.get("title") or step.get("label"), names)
    waiting = "; ".join(entry["line"][:1].lower() + entry["line"][1:] for entry in still)
    return Row(
        id=step["id"],
        label=step.get("label") or step["id"],
        name=name,
        step=f"{order}. {name}",
        delivers=plain(step.get("delivers"), names),
        state=state,
        blocker="" if done else plain(step.get("blocker"), names) or (f"Its open {'change is' if len(still) == 1 else 'changes are'}: {waiting}" if still else ""),
        unblocking="" if done else plain(step.get("unblocking") or (step.get("next") if step.get("tone") != "done" else None), names),
        went_wrong=plain(step.get("went_wrong"), names),
        done=done,
        moved=moved(doc, prs, [], after, names) if after else "",
    )


def milestone_row(release: dict, doc: dict, names: list[str], after: datetime | None) -> Row:
    number = found[1] if (found := MILESTONE.match(release["id"])) else release["id"]
    parts = release.get("parts") or []
    detail = plain(release.get("state"), names)
    done = detail.startswith("Live")
    name = plain(release.get("name"), names)
    return Row(
        id=release["id"],
        label=f"Milestone {number}",
        name=name,
        step=f"Milestone {number}: {name}",
        delivers=f"Updates the {', '.join(parts[:-1])} and {parts[-1]}" if len(parts) > 1 else f"Updates the {parts[0]}" if parts else "",
        state=stated("live" if done else "not started" if detail.lower().startswith("not started") else "in progress", detail),
        blocker="" if done else plain(release.get("blocker"), names),
        unblocking="" if done else plain(release.get("unblocking"), names),
        went_wrong=plain(release.get("went_wrong"), names),
        done=done,
        moved=moved(doc, [], [lane for lane in release.get("lanes") or [] if LANE_NAME.search(lane)], after, names) if after else "",
    )


def rows(doc: dict, after: datetime | None = None) -> list[Row]:
    names = every_lane(doc)
    built = [step_row(order, step, doc, names, after) for order, step in enumerate(doc["steps"], 1)]
    built += [milestone_row(release, doc, names, after) for release in doc["now"].get("releases") or []]
    return [row for row in built if row.moved] if after else built


def pick(found: list[Row], step: str) -> list[Row]:
    wanted = " ".join(step.lower().split())
    if wanted.isdigit():
        wanted = f"milestone {wanted}"
    return [row for row in found if wanted in (row.id.lower(), row.label.lower(), row.name.lower(), row.step.lower())]


def cell(text: str) -> str:
    return " ".join(text.split()).replace("|", "/")


def waits_on(row: Row) -> str:
    if row.blocker:
        return row.blocker
    return f"nothing; {row.unblocking[:1].lower()}{row.unblocking[1:]}" if row.unblocking else "nothing"


def left(found: list[Row]) -> list[dict]:
    return [{"step": row.step, "waits_on": waits_on(row)} for row in found if not row.done]


def markdown(doc: dict, found: list[Row], now: datetime, after: datetime | None, whole: bool) -> str:
    written = instant(doc["updated"])
    lines = [f"Plan status at {clock(now)}, from the progress record written at {clock(written)}."]
    milestones = [row for row in found if MILESTONE.match(row.id)]
    steps = [row for row in found if row not in milestones]
    if whole:
        lines.append(f"{sum(row.done for row in steps)} of {len(steps)} plan steps done; {sum(row.done for row in milestones)} of {len(milestones)} milestones live.")
    if after:
        lines.append(f"Only the rows that moved since {clock(after)}.")
    headings = (*HEADINGS, f"What moved since {clock(after)}") if after else HEADINGS
    lines += ["", "| " + " | ".join(headings) + " |", "|" + "---|" * len(headings)]
    lines += ["| " + " | ".join(cell(text) for text in row.cells()[: len(headings)]) + " |" for row in found]
    if whole and (remaining := left(found)):
        lines += ["", "## Left until completion", ""]
        lines += [f"- **{item['step']}** waits on: {item['waits_on']}" for item in remaining]
    return "\n".join(lines)


def render(path: Path, *, as_json: bool = False, step: str | None = None, after: str | None = None, now: datetime | None = None) -> tuple[str, int]:
    """The overview of the ``steps.json`` at ``path`` and its exit code: 1 when ``step`` names no row."""
    now = now or datetime.now(UTC)
    doc = json.loads(path.read_text())
    moment = since(after, now) if after else None
    found = rows(doc, moment)
    if step:
        found = pick(found, step)
        if not found:
            return f"no plan step or milestone named {step}", 1
    whole = not step and not moment
    if as_json:
        payload = {"at": now.isoformat(), "written_at": doc["updated"], "since": moment.isoformat() if moment else None, "rows": [asdict(row) for row in found]}
        return json.dumps(payload | ({"left": left(found)} if whole else {}), indent=2), 0
    return markdown(doc, found, now, moment, whole), 0
