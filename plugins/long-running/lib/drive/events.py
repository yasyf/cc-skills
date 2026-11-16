"""The records beside an event a post calls unexplained, which the one Jev read weighs the post against, by meta's fix of 736353: a status told him the writer pod's kill at 10:58 was unread while root's planned S2 pod delete, record 735744, was its cause."""

import json
import re
from datetime import datetime, timedelta
from pathlib import Path

from drive import fixes, reads, render, state

UNEXPLAINED = re.compile(r"\bunexplained\b|\bcause\b[^.\n]{0,30}\b(?:unread|unknown|unclear|not (?:yet )?(?:read|known|clear))\b|\bno cause\b|\bnot sure (?:why|what caused)\b", re.IGNORECASE)
CLOCK = re.compile(r"\b(\d{1,2}):(\d{2})\s*([AaPp])?\.?[Mm]?\.?")
EVENT_WINDOW = timedelta(minutes=2)
EVENT_RECORDS = 30
EVENT_TEXT = 300
EVENT_BUDGET = 60000
EVENT_SECONDS = 5
UNEXPLAINED_RULE = (
    "Does `post.text` call an event at a stated time unexplained, or its cause unread or unknown, without citing one of `records`,"
    " the drive's records from within two minutes of that time, case attrs first, as the event's cause, or saying none of them applies?"
)


def event_times(text: str, moment: datetime) -> list[datetime]:
    """Each clock time a sentence calling an event unexplained, or its cause unread, states, read as today's Pacific time, the latest past one when it names no AM or PM."""
    pacific = moment.astimezone(fixes.PACIFIC)
    times = []
    for sentence in re.split(r"(?<=[.!?\n])\s+", text):
        if not UNEXPLAINED.search(sentence):
            continue
        for found in CLOCK.finditer(sentence):
            hour, minute, half = int(found[1]) % 12, int(found[2]), (found[3] or "").lower()
            choices = [hour + 12] if half == "p" else [hour] if half == "a" else [hour, hour + 12]
            stamps = [pacific.replace(hour=each, minute=minute, second=0, microsecond=0) for each in choices]
            past = [stamp for stamp in stamps if stamp <= pacific] or stamps
            times.append(max(past))
    return times


def event_records(name: str, order_id: str | None, times: list[datetime]) -> list[dict]:
    """The drive's records from within EVENT_WINDOW of each time, the order's alone when the post names one, those carrying a case attr first, each cut to EVENT_TEXT, so Jev weighs whether one explains the event, as root's planned S2 pod delete 735744 explained the writer pod kill 736258 called unread."""
    found: dict[int, dict] = {}
    for at in times:
        start, end = at - EVENT_WINDOW, at + EVENT_WINDOW
        listed = reads.run(["cci", "tail", "--drive", name, "--since", start.isoformat(), "--oldest", "--budget", str(EVENT_BUDGET), "--json"], Path.cwd())
        for line in listed.splitlines():
            if not line.startswith("{"):
                continue
            record = json.loads(line)
            attrs = (record.get("fields") or {}).get("attrs") or {}
            if state.parse_time(record["at"]) <= end and (order_id is None or attrs.get("order") == order_id or order_id in record["text"]):
                found[record["seq"]] = {"seq": record["seq"], "at": render.when(record["at"]), "lane": record["lane"], "kind": record["kind"], "attrs": attrs, "text": record["text"][:EVENT_TEXT]}
    return sorted(found.values(), key=lambda record: ("case" not in record["attrs"], record["seq"]))[:EVENT_RECORDS]
