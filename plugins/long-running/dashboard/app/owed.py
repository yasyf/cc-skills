import collections
import datetime as dt
import re
from zoneinfo import ZoneInfo

PACIFIC = ZoneInfo("America/Los_Angeles")
LEDGER_ROW = re.compile(r"\| m(\d+) \| (\w+ \d+ \d+:\d+ [AP]M) \| (.*) \| (\S*) \| (\S*|due [^|]*?) \|$")
ANSWERED = "Answered, nothing for you."
MENTION = re.compile(r"\bm(\d{1,3})\b")
ITEM = re.compile(r"\bM(\d{1,3})\b")
PR_NAMED = re.compile(r"(?<![\w/])#(\d{5})\b")
REPO_PR = re.compile(r"(yasyf/[\w-]+)(?:#|/pull/)(\d+)")
LANE = re.compile(r"\b([a-z][a-z-]+-\d+)\b")
TAP = re.compile(r"Owner on board \w+, (.*?)(?:, [A-Z][^,]*\?)? \(([\w:-]+)\)(?:: (picked .+?|\d+ approved at \w+))?(?:, at [^,]*)?$")
RELAY_LANE = re.compile(r"(root|owner-relay|orca-desk|meta|supervisor|board-watch|signal-\d+|.*-desk(-\d+)?|board-refresher(-\d+)?)")
TAP_LEAD = re.compile(r"^Owner on board \w+, ")
TAP_ANSWER = re.compile(r" \([\w:-]+\)(?:: .*)?$", re.S)
OVERDUE_HEAD = re.compile(r"^\d+ overdue:$", re.M)
OVERDUE_ROW = re.compile(r"^- (?:(unfinished) (\w+)/(\d+)|(ask) #(\d+)|(repeat) (.+?)), due (.+)$")
SEQ_KINDS = {"pick", "finding"}
MIN_ASK = 14
KEPT_TEXT = 600


def kept(record):
    return record["kind"] in {"ask", "owner", "defect"} or bool(MENTION.search(record["text"]) or ITEM.search(record["text"]))


def trimmed(record):
    return {"seq": record["seq"], "lane": record["lane"], "kind": record["kind"], "at": record["at"], "text": record["text"][:KEPT_TEXT], "refs": record.get("refs")}


def stamp_iso(stamp, year):
    return dt.datetime.strptime(f"{year} {stamp}", "%Y %b %d %I:%M %p").replace(tzinfo=PACIFIC).isoformat()


def worker_records(records):
    return [r for r in records if r["lane"] and not RELAY_LANE.fullmatch(r["lane"])]


def named_prs(records):
    numbers = set()
    for record in records:
        numbers.update(str(ref) for ref in (record.get("refs") or {}).get("prs") or [])
        numbers.update(PR_NAMED.findall(record["text"]))
        numbers.update(f"{repo}#{number}" for repo, number in REPO_PR.findall(record["text"]))
    return sorted(numbers)


def owner_ask_rows(ledger, records, prs, store, known_lanes, year, answers):
    lines = ledger.splitlines()
    done = [r for r in worker_records(records) if r["kind"] == "done"]
    by_message = collections.defaultdict(list)
    for record in records:
        for number in set(MENTION.findall(record["text"])):
            by_message[int(number)].append(record)
    asks = []
    taps = []
    for index, line in enumerate(lines):
        if not (row := LEDGER_ROW.match(line)):
            continue
        number, stamp, mark = int(row[1]), row[2], row[5]
        lines_said = [said.strip() for said in row[3].split("<br>") if said.strip()]
        text = " ".join(said for said in lines_said if not said.startswith(">")) or " ".join(lines_said)
        if len(text) < MIN_ASK:
            continue
        iso = stamp_iso(stamp, year)
        if text.startswith("Owner on board"):
            taps.append(tap_row(number, iso, text, prs))
            continue
        mine = by_message[number]
        follow_up = " ".join(following for following in lines[index + 1 : index + 3] if not following.startswith("| m"))
        items = set(ITEM.findall(follow_up))
        for record in mine:
            items.update(re.findall(rf"\bm{number}\b[^.;]{{0,80}}?\bM(\d{{1,3}})\b", record["text"]))
        finished = [r for r in done if set(ITEM.findall(r["text"])) & items or number in {int(found) for found in MENTION.findall(r["text"])}]
        workers = worker_records(mine)
        named = named_prs([*workers, *finished])[:3]
        words = " ".join(r["text"] for r in mine) + " " + follow_up
        holders = list(dict.fromkeys([*[lane for lane, _ in collections.Counter(r["lane"] for r in workers).most_common(3)], *[r["lane"] for r in finished], *sorted(set(LANE.findall(words)) & known_lanes)]))[:3]
        answer = answers.get(f"m{number}", "")
        state = message_state(mark, finished, mine, named, prs, answer)
        asks.append({
            "m": number,
            "iso": iso,
            "text": text,
            "state": state,
            "ack": ack_state(row[4], mark, state),
            "lanes": holders,
            "prs": named,
            "answer": answer or (ANSWERED if state == "answered" else ""),
        })
    return {"messages": asks, "taps": taps, "store": store}


def tap_row(number, iso, text, prs):
    found = TAP.search(text)
    said = found[3] if found and found[3] else ""
    approved = re.match(r"(\d+) approved", said)
    return {
        "m": number,
        "iso": iso,
        "card": found[2] if found else "",
        "said": said,
        "question": found[1] if found else text,
        "pr": approved[1] if approved else "",
        "state": "applied" if approved and (prs.get(approved[1]) or {}).get("state") == "landed" else "answered",
    }


def order_replies(replies):
    return {entry["reply"]["order"]: entry["reply"] for entry in reversed(replies) if entry["reply"].get("order")}


def told_asks(ledger, told):
    return {int(row[4]): told[f"m{row[1]}"] for row in map(LEDGER_ROW.match, ledger.splitlines()) if row and row[4].isdigit() and f"m{row[1]}" in told}


def message_state(mark, finished, records, named, prs, answer):
    if finished or any((prs.get(number) or {}).get("state") == "landed" for number in named):
        return "applied"
    if answer or mark == "responded" or any(r["kind"] in {"answer", "report"} for r in records):
        return "answered"
    return "asked"


def ack_state(routed, mark, state):
    if state != "asked" or mark in {"addressed", "no-order"}:
        return "done"
    return "routed" if routed or mark.startswith("due") else "received"


def overdue_rows(render, records, store):
    head = OVERDUE_HEAD.search(render)
    if not head:
        return []
    by_seq = {r["seq"]: r for r in records}
    by_key = {row["key"]: row["text"] for row in store}
    rows = []
    for line in render[head.end() :].strip().split("\n\n")[0].splitlines():
        found = OVERDUE_ROW.match(line)
        kind = found[2] or found[4] or found[6]
        key = found[3] or found[5] or ""
        seq = int(key) if found[4] or kind in SEQ_KINDS else None
        record = by_seq.get(seq)
        words = found[7] or by_key.get(f"{kind}/{key}") or (TAP_ANSWER.sub("", TAP_LEAD.sub("", record["text"])) if record else f"{kind} {int(key)}")
        rows.append({"id": f"{kind}-{key or found[7]}", "group": kind, "seq": seq, "due": found[8], "text": words})
    return rows
