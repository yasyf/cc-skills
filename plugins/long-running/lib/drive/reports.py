import itertools
import json
import re
from collections.abc import Iterator
from datetime import datetime

from drive import agents, artifacts, limits, refs, settled, slack, state

CALL_FIELDS = ("records", "lane", "drive", "id")
OPENING = re.compile(r"(?:POST|FYI)\b")
UNOPENED = "refused: a report's text opens `POST`, for a handler to post, or `FYI`, kept as context; add the one it is"
UNFINISHED = (
    "refused: this POST report names nothing its post finishes, so its handler cannot tell which order to close; add"
    " `--attr close=<o,…>` naming each order it finishes{about}, `--attr part=<o>:<part>` naming the part it delivers{parts},"
    " or `--attr progress=true` on a progress line that finishes nothing"
)
LAST_PART = (
    "refused: this report delivers {parts}, the last {noun} of {order} not yet delivered, so its post finishes the order; add"
    " `--attr close={order}`, so its handler closes {order} once the line is posted"
)
STRAY_PARTS = (
    "refused: the drive reads no {strays} attr on a report, so the part it carries is never delivered; one `part` attr carries every"
    " part, each `<o>:<part>` with `;` between, as `part=<o>:<a>;<o>:<b>`, so post it again with `--attr part=\"{parts}\"` in place of {carriers}"
)
UNKNOWN_ORDER = "refused: the part attr's item {item} names {order}, no order of drive {drive}; name each part as `<o>:<part>`, items separated by `;`"
UNKNOWN_PART = 'refused: {order} has no part "{part}", so this post would deliver nothing there; its parts: {parts}'
UNPOSTED_PART = (
    'refused: {work}; drop "{order}:{part}" from the part attr, adding `--attr progress=true` when the post delivers nothing else,'
    " or close {order} with `--attr close={order}` once its work is done"
)
PLAIN_INCIDENT = (
    "refused: this report is marked incident=true, and {why}; the drive tells an incident's line from routine news only by its"
    " order's incident flag, so every check, his away hold included, would treat this line as routine. Open the outage with"
    " `drive incident open`, or mark its order with `drive order set <o> --incident`, and send the report again naming that"
    " order; a customer team's outage the drive did not cause is no incident and carries `--attr customer_outage=true` instead,"
    " which posts it while he is away; drop the mark when the line is neither"
)
UNASKED_CLOSE = (
    "refused: {order} is drive tooling a session opened, with no message or thread of his, so its close posts him no line:"
    " close it with `drive order set {order} --state done --proof <its proof>`, and the dashboard shows it live"
)
THREAD_TS = re.compile(r"\d{10}\.\d{6}")
NOT_THREAD = "refused: thread {thread} is no Slack thread, which is named by its root message's ts, such as 1794478810.620779; leave thread out for a post at his DM root"
NOT_UNTIL = "refused: until {until} is no time with its offset; name the end of the post it goes with in RFC 3339, such as 2026-11-14T13:30:00-08:00"
SECOND_REPORT = (
    "refused: the coordinator and the meta session send one report per thread per turn, a wake to the end of its response;"
    " this turn already sent one for thread {thread}, so the rest waits for the next wake."
    " A report on an open incident goes, as does one correcting a withdrawn report"
)
SECOND_REPORT_RULE = "A second report for a thread in one turn of the coordinator's or the meta session's that repeats what its first report there already sent, rather than news that came in since; a report on an open incident order always goes."
UNPREVENTED = (
    "record_lines: this report goes out as written, but these POST lines may tell him the drive failed or ran late, naming no"
    " prevention. If one does, start its fix and resend that line naming the order or PR URL fixing it, not the"
    " report's own; a line about what the product does needs nothing.\n```\n{lines}\n```"
)
ON_HIM = re.compile(
    r"\bwait(?:s|ing)? (?:on|for) (?:him|his|your|you)\b|\b(?:needs?|asks?|wants?) (?:his|your) (?:tap|pick|answer|approval|word|go|ok|yes)\b"
    r"|\byes or no\b|\by/n\b|\breply (?:with )?(?:yes|no|ok|go|\d)\b|\b(?:pick|choose) (?:one|between|an option|\d)\b"
    r"|\b(?:may|shall|should|can) (?:i|we|the drive|it)\b[^?\n]*\?|\b(?:ok|okay) (?:to|if)\b[^?\n]*\?|\b(?:do you want|want me to|would you like)\b[^?\n]*\?",
    re.IGNORECASE,
)
CARD_NAMED = re.compile(r"slack\.com/archives/|\{card:|\b[\w-]+:j\d+-\d+\b|github\.com/[^/\s]+/[^/\s]+/pull/\d+")
UNCARDED = (
    "refused: this report waits on him or asks him to decide, a yes or no or a pick, and names no card, so its handler would post"
    " it as plain text with no buttons; keep a question no card asks yet with `drive job ask <j> <file>`, which draws his card"
    " with its buttons, and report no question to him; a report on a card already up names it: its permalink, the PR whose card"
    " it is, or its waits id with `--attr waits=<drive>:<j>-<n>`, so the handler links the card"
)
UNCARDED_RULE = (
    "A decision the drive needs from him, anything waiting on him, a yes or no or a pick between options, reaches him as a"
    ' question card with its buttons, never as a report\'s plain text, by his words "Where\'s the orca card?" and "Anything that'
    ' says waiting on me you always need to embed the card with the buttons".'
)
WAITS_ON_OTHER = re.compile(r"\bwait(?:s|ing)? on (?!(?:him|his|your|you|he)\b)\S", re.IGNORECASE)
WAITED = re.compile(r"\b\d+ ?(?:s|secs?|seconds?|m|mins?|minutes?|h|hrs?|hours?|d|days?)\b|\bsince \d", re.IGNORECASE)
RUNNING = re.compile(r"\b(?:running|queued|idle|stopped|started|starting|in progress|not started)\b", re.IGNORECASE)
VAGUE_WAIT = (
    "refused: a line that says something waits on X says how long it has waited and whether X is running, such as \"waits on its staging test,"
    " the test job running since {started:<job>}\": name the job doing X as {started:<job>} or the record the wait began on as {at:<seq>},"
    " whose stamp say fills, so no time is typed; add both"
)
DM_URL = (
    "refused: a report's --url is the link of what it measured, such as a trace, notebook, log or build, which {{record:<seq>|…}}"
    " links in his posts, and {url} is a message in his DM, which --attr thread={thread} names; drop --url, or put that link there"
)


def kind(record: dict) -> str:
    return (record.get("kind") or "").lower()


def attrs(record: dict) -> dict:
    return record.get("attrs") or {}


def posted(tool_input: dict) -> list[dict]:
    """Each record a cci `post` call stores: the call itself, or each record of its records list filled in as cci's post tool fills it, every field beside the list the record lacks copied in and the attrs beside the list under its own, the call's lane, drive and idempotency key aside, which cci keeps for the whole call."""
    if not (records := tool_input.get("records")):
        return [tool_input]
    beside = {key: value for key, value in tool_input.items() if key not in CALL_FIELDS}
    return [beside | record | ({"attrs": beside["attrs"] | attrs(record)} if "attrs" in beside else {}) for record in records]


def listed(value: object) -> list[str]:
    return [] if value is None else [str(item) for item in (value if isinstance(value, list) else [value])]


def addressed(record: dict, lane: str) -> bool:
    return any(entry.split("@", 1)[0] == lane for entry in listed(record.get("to")))


def relayed_time(record: dict, drive: dict) -> str | None:
    """Why a record whose words a handler posts to him, a POST report or a ready record to the communicator, types a clock time for work already done; any other record goes between sessions and types the times it quotes, by his words "i think that time hook its tooa ggessive too"."""
    text = record.get("text") or ""
    if kind(record) not in ("report", "ready") or not addressed(record, "communicator") or (kind(record) == "report" and not text.startswith("POST")):
        return None
    return agents.typed_time(f"{text}\n{record.get('body') or ''}", drive)


def hand_ask(record: dict) -> str | None:
    """Why an ask record does not go out, whether the cci `post` tool or `cci post` in Bash sends it, decided by its shape alone so every call gets the same verdict: it carries his card's ASK block, its stored first line opening `ASK ` or an `ask` or `waits` attr, which only `drive job ask` keeps and draws; any other ask is a question to another session's lane, whatever lane it names, and goes out."""
    carded = limits.opening_line(record.get("text") or "").startswith("ASK ") or {"ask", "waits"} & attrs(record).keys()
    return agents.Unjudged(settled.HAND_ASK) if kind(record) == "ask" and carded else None


def report_refusals(payload: dict, drive: dict, records: list[dict], index: int) -> Iterator[str | None]:
    """Why a report to the communicator does not go out as written, whether the cci `post` tool or `cci post` in Bash sends it, each check run only once the ones before it pass; a report on an open incident order is never held to one a thread a turn, by his words "this is incident so uyou should be faster and communicating more with me"."""
    record = records[index]
    if kind(record) != "report" or not addressed(record, "communicator"):
        return
    text = (record.get("text") or "").strip()
    thread = attrs(record).get("thread")
    posting = text.startswith("POST")
    yield None if OPENING.match(text) else UNOPENED
    yield dm_url(drive, record)
    yield next((agents.Unjudged(refused) for order_id in state.closed_orders(attrs(record)) if (refused := state.second_close(drive, order_id, attrs(record)))), None)
    yield next((agents.Unjudged(UNASKED_CLOSE.format(order=order_id)) for order_id in state.closed_orders(attrs(record)) if (order := drive["orders"].get(order_id)) and state.unasked_tooling(order)), None)
    yield misnamed_part(drive, record)
    yield plain_incident(drive, record)
    yield unfinished(drive, record) if posting else None
    yield not_thread(record)
    yield not_until(record)
    yield agents.Ruled(UNCARDED, UNCARDED_RULE) if ON_HIM.search(text) and not attrs(record).get("waits") and not CARD_NAMED.search(f"{text}\n{record.get('body') or ''}") else None
    yield agents.unpromised(drive["name"], f"{text}\n{record.get('body') or ''}")
    yield VAGUE_WAIT if WAITS_ON_OTHER.search(text) and not ((WAITED.search(text) or any(found[1] in limits.STAMPED for found in limits.REF.finditer(text))) and RUNNING.search(text)) else None
    if thread and state.session_role(drive, payload.get("session_id")) in ("coordinator", "meta") and not payload.get("agent_id") and not state.open_incidents(drive, state.report_orders(drive, attrs(record))):
        yield second_report(payload, drive["name"], records, index, thread)
    yield agents.unread_job(payload, text)
    yield agents.unowned_work(text)


def unprevented(drive: dict, record: dict) -> str | None:
    """What the session hears when a POST report to the communicator tells him a failure of the drive's with no word on how it is prevented, by his pick on the say hook's findings "1 and then if we kepe getting false rejects move to 3": the report goes out as written, since the pattern also matches the product's own refusals."""
    text = (record.get("text") or "").strip()
    if kind(record) != "report" or not addressed(record, "communicator") or not text.startswith("POST"):
        return None
    if not (lines := refs.unprevented(told := f"{text}\n{record.get('body') or ''}", refs.records(drive["name"], told), refs.subject(drive, state.report_order(drive, attrs(record))))):
        return None
    return UNPREVENTED.format(lines="\n".join(lines))


def dm_url(drive: dict, record: dict) -> str | None:
    """Why a report's url names a message in his DM: a report's url is what it measured, which {record:} links, and the message it answers is its thread attr."""
    found = state.CARD_LINK.search(record.get("url") or "")
    if not found or found[1] != slack.dm():
        return None
    return DM_URL.format(url=record["url"], thread=found[4] or f"{found[2]}.{found[3]}")


def stray_parts(kind: str, named: dict) -> str | None:
    """Why a report carries a part of an order in an attr other than part, the one every post reads its parts from: one whose name holds part, as part2, parts or part_2, or whose value names a part as `<o>:<part>`, so that part is never delivered; refused by the attrs alone, naming the part value that carries them all."""
    if kind != "report":
        return None
    if not (strays := [key for key, value in named.items() if key != "part" and ("part" in limits.squashed(key) or state.ORDER_PART.fullmatch(str(value).strip()))]):
        return None
    carriers = [key for key in ("part", *strays) if key in named]
    items = [str(named[key]).strip() for key in carriers]
    parts = ";".join(item if state.ORDER_PART.match(item) else f"{named.get('order') or '<o>'}:{item}" for item in items)
    return agents.Unjudged(STRAY_PARTS.format(strays=" or ".join(f"`{key}`" for key in strays), parts=parts, carriers=", ".join(f"`{key}`" for key in carriers)))


def misnamed_part(drive: dict, record: dict) -> str | None:
    """Why an item of a report's part attr would deliver nothing: it names an order the drive does not hold, a part its order does not have, or work he ordered, which no post delivers, on an order the report does not close."""
    closed = state.closed_orders(attrs(record))
    for order_id, part in state.part_items(attrs(record), state.report_order(drive, attrs(record))):
        if order_id is None:
            continue
        if order_id not in drive["orders"]:
            return agents.Unjudged(UNKNOWN_ORDER.format(item=json.dumps(f"{order_id}:{part}", ensure_ascii=False), order=order_id, drive=drive["name"]))
        if part not in (held := drive["orders"][order_id]["parts"]):
            return agents.Unjudged(UNKNOWN_PART.format(order=order_id, part=part, parts=", ".join(json.dumps(each, ensure_ascii=False) for each in held)))
        if order_id not in closed and state.work(drive, order_id, part):
            return agents.Unjudged(UNPOSTED_PART.format(work=state.UNPOSTED_WORK.format(order=order_id, part=part), order=order_id, part=part))
    return None


def unfinished(drive: dict, record: dict) -> str | None:
    """Why a report to post leaves its handler guessing what it finishes: it names no close, part or progress attr, or the parts it delivers are the last ones undelivered of an open order it does not close, which would leave that order open once its last answer is posted. An order of work he ordered names no part, since no post delivers it."""
    named = attrs(record)
    if named.get("catch_up") or named.get("progress"):
        return None
    order_id = state.report_order(drive, named)
    items = state.part_items(named, order_id)
    closed = state.closed_orders(named)
    if not items and not closed:
        order = state.open_orders(drive).get(order_id or "")
        quoted = ", ".join(f'"{part}"' for part, kept in order["parts"].items() if not kept["posted"] and not state.work(drive, order_id, part)) if order else ""
        return UNFINISHED.format(about=f", here {order_id}, the open order it is about" if order else "", parts=f", one of {quoted}" if quoted else "")
    for on in dict.fromkeys(on for on, _ in items if on not in closed):
        order = state.open_orders(drive).get(on or "")
        if not order or any(state.work(drive, on, part) for part in order["parts"]):
            continue
        left = [part for part, kept in order["parts"].items() if not kept["posted"]]
        if left and set(left) <= {part for each, part in items if each == on}:
            return LAST_PART.format(parts=", ".join(f'"{part}"' for part in left), noun="part" if len(left) == 1 else "parts", order=on)
    return None


def plain_incident(drive: dict, record: dict) -> str | None:
    """Why a report marked incident=true goes nowhere: none of the orders it names is an open incident, and only an order's incident flag makes a line an incident's to the drive's checks."""
    if attrs(record).get("incident") != "true":
        return None
    orders = state.report_orders(drive, attrs(record))
    if state.open_incidents(drive, orders):
        return None
    why = f"{', '.join(orders)} {'is no open incident' if len(orders) == 1 else 'are no open incidents'}" if orders else "it names no order"
    return agents.Unjudged(PLAIN_INCIDENT.format(why=why))


def not_thread(record: dict) -> str | None:
    """Why a report names a thread that is no Slack thread, refused by its shape alone, so every time."""
    if (thread := attrs(record).get("thread")) and not THREAD_TS.fullmatch(thread):
        return agents.Unjudged(NOT_THREAD.format(thread=thread))
    return None


def not_until(record: dict) -> str | None:
    """Why a report names an end that is no time with its offset, refused by its shape alone, so every time."""
    if not (until := attrs(record).get("until")):
        return None
    try:
        if datetime.fromisoformat(until).tzinfo:
            return None
    except ValueError:
        pass
    return agents.Unjudged(NOT_UNTIL.format(until=until))


def report_threads(tool_input: dict) -> set[str]:
    return {attrs(record).get("thread") for record in posted(tool_input) if kind(record) == "report" and addressed(record, "communicator")} - {None}


def second_report(payload: dict, name: str, records: list[dict], index: int, thread: str) -> str | None:
    """Why the coordinator's or the meta session's report for a thread waits for its next turn: the batch, or a post since its last response ended, already carried one for that thread that no withdraw record answers, by agents.amendments, so a report withdrawn for what it got wrong leaves its turn's place to the report correcting it. A post's result names the seq of each record it stored, in the order of its records; one whose result names none counts."""
    if thread in report_threads({"records": records[:index]}):
        return agents.Ruled(SECOND_REPORT.format(thread=thread), SECOND_REPORT_RULE)
    sent: dict[str, list[str | None]] = {}
    seqs: list[str | None] = []
    for row in agents.tail(agents.transcript_of(payload)):
        if agents.ended(row):
            sent, seqs = {}, []
        for part in agents.parts(row):
            if part.get("type") == "tool_use" and part.get("name") == payload["tool_name"] and part.get("id") != payload.get("tool_use_id"):
                sent[part["id"]] = [attrs(record).get("thread") if kind(record) == "report" and addressed(record, "communicator") else None for record in posted(part.get("input") or {})]
            elif part.get("type") == "tool_result" and part.get("tool_use_id") in sent and not part.get("is_error"):
                stored = [word for word in artifacts.tool_result_text(part.get("content")).split() if word.isdigit()]
                seqs += [seq for seq, said in itertools.zip_longest(stored, sent[part["tool_use_id"]]) if said == thread]
    if seqs and (None in seqs or not set(seqs) <= agents.amendments(name, seqs)["withdraw"].keys()):
        return agents.Ruled(SECOND_REPORT.format(thread=thread), SECOND_REPORT_RULE)
    return None
