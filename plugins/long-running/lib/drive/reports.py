import json
import re
from collections.abc import Iterator

from drive import agents, refs, slack, state

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
UNKNOWN_ORDER = "refused: the part attr's item {item} names {order}, no order of drive {drive}; name each part as `<o>:<part>`, items separated by `;`"
UNKNOWN_PART = 'refused: {order} has no part "{part}", so this post would deliver nothing there; its parts: {parts}'
UNLIVE_PART = (
    'refused: {unlive}; drop "{order}:{part}" from the part attr, adding `--attr progress=true` when the post delivers nothing else,'
    " or close the incident with `--attr close={order}` once the fix runs where it hurt"
)
SKIPPED = (
    "refused: {answered}, and no post delivered {them}, so this post would go out and leave {them} unposted, which the watch raises"
    " every 30 minutes; carry {them} in this post with `--attr part=\"{items}\"`, or `--attr close=<o>` on the post that finishes an"
    " order; when a post of the drive already delivered {them}, the session holding the order marks it first with {marks}"
)
OFF_THREAD = (
    "refused: this report is about {orders}, and thread {thread} is none of its threads: {threads}. Name one of those threads,"
    " or pass re naming the record of his message in this thread that the report answers, and its post adds this thread to them"
)
THREAD_TS = re.compile(r"\d{10}\.\d{6}")
NOT_THREAD = "refused: thread {thread} is no Slack thread, which is named by its root message's ts, such as 1794478810.620779; leave thread out for a post at his DM root"
SECOND_REPORT = (
    "refused: the coordinator and the meta session send one report per thread per turn, a turn running from a wake to the end of its response,"
    " and this turn already sent one for thread {thread}; put everything for that thread in that one report, and what"
    " does not fit waits for the next wake. A report on an open incident is never held so"
)
SECOND_REPORT_RULE = "A second report for a thread in one turn of the coordinator's or the meta session's that repeats what its first report there already sent, rather than news that came in since; a report on an open incident order always goes."
UNPREVENTED = (
    "record_lines: this report goes out as written, but these POST lines may tell him the drive failed with no word on how that"
    " is prevented. If one does, start its fix and send that line again naming the tool change and the order or PR URL"
    " carrying it; a line about what the product does needs nothing.\n```\n{lines}\n```"
)
WAITS_ON_HIM = re.compile(r"\bwait(?:s|ing)? (?:on|for) (?:him|his|your|you)\b|\bneeds? (?:his|your) (?:tap|pick|answer|approval)\b", re.IGNORECASE)
CARD_NAMED = re.compile(r"slack\.com/archives/|\{card:|\b[\w-]+:j\d+-\d+\b|github\.com/[^/\s]+/[^/\s]+/pull/\d+")
UNCARDED = "refused: this report says something waits on him and names no card; add its permalink, the PR whose card it is, or its waits id with `--attr waits=<drive>:<j>-<seq>`, so the handler links the card"
WAITS_ON_OTHER = re.compile(r"\bwait(?:s|ing)? on (?!(?:him|his|your|you|he)\b)\S", re.IGNORECASE)
WAITED = re.compile(r"\b\d+ ?(?:s|secs?|seconds?|m|mins?|minutes?|h|hrs?|hours?|d|days?)\b|\bsince \d", re.IGNORECASE)
RUNNING = re.compile(r"\b(?:running|queued|idle|stopped|started|starting|in progress|not started)\b", re.IGNORECASE)
VAGUE_WAIT = "refused: a line that says something waits on X says how long it has waited and whether X is running, such as \"waits on CI, 40 min, run 123 running\"; add both"
DM_URL = (
    "refused: a report's --url is the link of what it measured, such as a trace, notebook, log or build, which {{record:<seq>}}"
    " links in his posts, and {url} is a message in his DM, which --attr thread={thread} names; drop --url, or put that link there"
)


def kind(record: dict) -> str:
    return (record.get("kind") or "").lower()


def attrs(record: dict) -> dict:
    return record.get("attrs") or {}


def listed(value: object) -> list[str]:
    return [] if value is None else [str(item) for item in (value if isinstance(value, list) else [value])]


def addressed(record: dict, lane: str) -> bool:
    return any(entry.split("@", 1)[0] == lane for entry in listed(record.get("to")))


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
    yield misnamed_part(drive, record)
    yield unfinished(drive, record) if posting else None
    yield skipped(drive, record) if posting else None
    yield off_thread(drive, record)
    yield UNCARDED if WAITS_ON_HIM.search(text) and not attrs(record).get("waits") and not CARD_NAMED.search(f"{text}\n{record.get('body') or ''}") else None
    yield VAGUE_WAIT if WAITS_ON_OTHER.search(text) and not (WAITED.search(text) and RUNNING.search(text)) else None
    if thread and state.session_role(drive, payload.get("session_id")) in ("coordinator", "meta") and not payload.get("agent_id") and not state.open_incidents(drive, state.report_orders(drive, attrs(record))):
        yield second_report(payload, records, index, thread)
    yield agents.unread_job(payload, text)


def unprevented(drive: dict, record: dict) -> str | None:
    """What the session hears when a POST report to the communicator tells him a failure of the drive's with no word on how it is prevented, by his pick on the say hook's findings "1 and then if we kepe getting false rejects move to 3": the report goes out as written, since the pattern also matches the product's own refusals."""
    text = (record.get("text") or "").strip()
    if kind(record) != "report" or not addressed(record, "communicator") or not text.startswith("POST"):
        return None
    if not (lines := refs.unprevented(told := f"{text}\n{record.get('body') or ''}", refs.records(drive["name"], told))):
        return None
    return UNPREVENTED.format(lines="\n".join(lines))


def dm_url(drive: dict, record: dict) -> str | None:
    """Why a report's url names a message in his DM: a report's url is what it measured, which {record:} links, and the message it answers is its thread attr."""
    found = state.CARD_LINK.search(record.get("url") or "")
    if not found or found[1] != drive["slack"]["channel"]:
        return None
    return DM_URL.format(url=record["url"], thread=found[4] or f"{found[2]}.{found[3]}")


def misnamed_part(drive: dict, record: dict) -> str | None:
    """Why an item of a report's part attr would deliver nothing: it names an order the drive does not hold, a part its order does not have, or the fix of an open incident the watch has not recorded live on an order the report does not close."""
    closed = state.closed_orders(attrs(record))
    for order_id, part in state.part_items(attrs(record), state.report_order(drive, attrs(record))):
        if order_id is None:
            continue
        if order_id not in drive["orders"]:
            return agents.Unjudged(UNKNOWN_ORDER.format(item=json.dumps(f"{order_id}:{part}", ensure_ascii=False), order=order_id, drive=drive["name"]))
        if part not in (held := drive["orders"][order_id]["parts"]):
            return agents.Unjudged(UNKNOWN_PART.format(order=order_id, part=part, parts=", ".join(json.dumps(each, ensure_ascii=False) for each in held)))
        if order_id not in closed and state.unlive_fix(drive, order_id, part):
            return agents.Unjudged(UNLIVE_PART.format(unlive=state.UNLIVE_FIX.format(order=order_id, part=part), order=order_id, part=part))
    return None


def unfinished(drive: dict, record: dict) -> str | None:
    """Why a report to post leaves its handler guessing what it finishes: it names no close, part or progress attr, or the parts it delivers are the last ones undelivered of an open order it does not close, which would leave that order open once its last answer is posted. An incident's last part is exempt, since its order closes when its fix ships."""
    named = attrs(record)
    if named.get("catch_up") or named.get("progress"):
        return None
    order_id = state.report_order(drive, named)
    items = state.part_items(named, order_id)
    closed = state.closed_orders(named)
    if not items and not closed:
        order = state.open_orders(drive).get(order_id or "")
        quoted = ", ".join(f'"{part}"' for part, kept in order["parts"].items() if not kept["posted"]) if order else ""
        return UNFINISHED.format(about=f", here {order_id}, the open order it is about" if order else "", parts=f", one of {quoted}" if quoted else "")
    for on in dict.fromkeys(on for on, _ in items if on not in closed):
        order = state.open_orders(drive).get(on or "")
        if not order or order["incident"]:
            continue
        left = [part for part, kept in order["parts"].items() if not kept["posted"]]
        if left and set(left) <= {part for each, part in items if each == on}:
            return LAST_PART.format(parts=", ".join(f'"{part}"' for part in left), noun="part" if len(left) == 1 else "parts", order=on)
    return None


def skipped(drive: dict, record: dict) -> str | None:
    """Why a report to post leaves out a part of an open order it is about that a finished job answered and no post delivered: its post would go out while that answer stays unposted, the gap the watch raises until someone marks the part by hand."""
    named = attrs(record)
    about = state.report_order(drive, named)
    items = state.part_items(named, about)
    closed = state.closed_orders(named)
    orders = [order_id for order_id in dict.fromkeys([about, *(on for on, _ in items)]) if order_id in state.open_orders(drive) and order_id not in closed]
    left = [(order_id, part, jobs) for order_id in orders for part, jobs in state.answered_parts(drive, order_id).items() if (order_id, part) not in items]
    if not left:
        return None
    answered = "; ".join(f'{order_id}\'s "{part}" was answered by {", ".join(jobs)}, which finished' for order_id, part, jobs in left)
    carried = ";".join(f"{order_id}:{part}" for order_id, part in [*((on, part) for on, part in items if on), *((order_id, part) for order_id, part, _ in left)])
    marks = ", ".join(f'`drive order posted {order_id} <that post\'s link> --part "{part}"`' for order_id, part, _ in left)
    return agents.Unjudged(SKIPPED.format(answered=answered, them="it" if len(left) == 1 else "them", items=carried, marks=marks))


def off_thread(drive: dict, record: dict) -> str | None:
    """Why a report names a thread that is no Slack thread, refused by its shape alone, so every time, or none of the threads of an order it is about, finishes or delivers a part of, nor the thread of the message of his it answers through re, nor one under a card of a PR such an order holds: `say` would refuse its post there, and only its writer can name the message it answers."""
    named = attrs(record)
    if not (thread := named.get("thread")):
        return None
    if not THREAD_TS.fullmatch(thread):
        return agents.Unjudged(NOT_THREAD.format(thread=thread))
    held = {order: own for order in state.report_orders(drive, named) if (own := [ts for _, ts in slack.order_threads(drive, order, drive["orders"][order])])}
    threads = list(dict.fromkeys(ts for own in held.values() for ts in own))
    if not held or thread in threads:
        return None
    if (asked := agents.asked_thread(listed(record.get("re")))) and asked[1] == thread:
        return None
    root = slack.root(drive["slack"]["channel"], thread)
    if any(slack.names_order_pr(drive, order, root) for order in held):
        return None
    return OFF_THREAD.format(orders=", ".join(held), thread=thread, threads=", ".join(threads))


def report_threads(tool_input: dict) -> set[str]:
    return {attrs(record).get("thread") for record in tool_input.get("records") or [tool_input] if kind(record) == "report" and addressed(record, "communicator")} - {None}


def second_report(payload: dict, records: list[dict], index: int, thread: str) -> str | None:
    """Why the coordinator's or the meta session's report for a thread waits for its next turn: the batch, or a post since its last response ended, already carried one for that thread."""
    if thread in report_threads({"records": records[:index]}):
        return agents.Ruled(SECOND_REPORT.format(thread=thread), SECOND_REPORT_RULE)
    sent: dict[str, set[str]] = {}
    posted: set[str] = set()
    for row in agents.tail(agents.transcript_of(payload)):
        if agents.ended(row):
            sent, posted = {}, set()
        for part in agents.parts(row):
            if part.get("type") == "tool_use" and part.get("name") == payload["tool_name"] and part.get("id") != payload.get("tool_use_id"):
                sent[part["id"]] = report_threads(part.get("input") or {})
            elif part.get("type") == "tool_result" and part.get("tool_use_id") in sent and not part.get("is_error"):
                posted.add(part["tool_use_id"])
    return agents.Ruled(SECOND_REPORT.format(thread=thread), SECOND_REPORT_RULE) if any(thread in sent[call] for call in posted) else None
