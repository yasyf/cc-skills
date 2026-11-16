"""Where each message of his goes, once the drive has his words: one tool-less Jev call over his message, his thread's rows and the open routes picks the coordinator, a seat below it working an order, a handler, or the communicator's own answer from the rows, by his words "maybe we can use jev on every message to determine do we route to the coordinator, a sub-coordinator, a handler from the pool (with stickiness for relevant threads, can maintain a small lookup table or tiny tiny semantic search that is very lightweight), or the communicator itself can handle it"."""

import json
import os
import time
from contextlib import contextmanager
from datetime import timedelta

from drive import agents, settled, state
from drive.render import cut

NEW = "handler:new"
COMMUNICATOR = "communicator"
COORDINATOR = "coordinator"
SEAT = "seat:"
HANDLER = "handler:"
SEATS = 12
SIMILAR = 0.5
ROWS = 8
ROW_CHARS = 300
WORDS_CHARS = 2000
FOLLOW_WINDOW = timedelta(minutes=30)
ROUTED_WAIT = 120
ON_IT = "On it, working on this now."
CONTRACT = (
    "The router's Jev input holds exactly: `message.words`, his words cut to WORDS_CHARS, and `thread`, the newest ROWS of the drive's rows"
    " for his thread, each cut to ROW_CHARS, by line number, with the open routes as the Pick's options, one line each naming what it holds. The follow-up"
    " read holds exactly `message.words` and `earlier.words`, the two messages' words, each cut to WORDS_CHARS. Nothing else of the drive goes in."
)
KEPT = timedelta(days=7)
LOG = "routes.log"
ASK = (
    "Which option should take `message.words`, his message, as `thread`, the drive's newest rows for the thread he wrote it in, reads them?"
    " Pick the one whose work his words are about, by what each option says it holds."
)
AWAY = settled.Noul("Do `message.words` say he is going to sleep, going offline or stepping away?")
FOLLOWS = settled.Noul(
    "Is `message.words` a follow-up to `earlier.words`, his message just before it in the same thread: adding to, narrowing, correcting or repeating the same request, so one reply should cover both?",
    no="It asks or orders something new that stands on its own.",
)
MEANINGS = {
    NEW: (
        "A fresh handler, which reads, posts and records for him: his question the rows do not answer, a share, a card, a post or"
        " picture he asks for, his ruling or feedback on how the drive, its posts or cards work, which it hands to the meta session,"
        " his words under a question card, or anything no other route plainly holds."
    ),
    COMMUNICATOR: (
        "The communicator answers at once from `thread` alone: a read-only question whose answer `thread` already prints, such as"
        " where work stands, what passed, failed or waits on what, or chatter such as ok, thanks or nice."
    ),
    COORDINATOR: "The top coordinator: an order, approval, rework or correction of his about his product work that no seat below works.",
}


def seats(data: dict) -> dict[str, str]:
    """Each open order a coordinator seat below the top one works, a priority seat's own or a project sub-coordinator's, newest moved first, at most SEATS of them, as a route of its own beside what it means."""
    held = [
        (order_id, order, seat)
        for order_id, order in state.open_orders(data).items()
        if state.holder_lane(order) is None and (seat := state.coordinator_seat(data, order)) in data["subcoordinators"]
    ]
    held.sort(key=lambda found: state.parse_time(found[1]["updated"]), reverse=True)
    return {
        f"{SEAT}{order_id}": f'The coordinator seat "{seat}", working order {order_id} "{cut(order["title"] or order["text"], 120)}": his order, approval, rework or correction on that work.'
        for order_id, order, seat in held[:SEATS]
    }


def handlers(data: dict) -> dict[str, str]:
    """Each live handler as a route of its own, beside the thread, his words or record it holds."""
    return {
        f"{HANDLER}{handler_id}": f"The pool handler holding {holding(entry)}: more of his words on what it holds or answered."
        for handler_id, entry in data["handlers"].items()
        if entry["session"] and not agents.gone(entry)
    }


def holding(entry: dict) -> str:
    held = entry["holds"] or {}
    words = agents.his_words(entry["brief"]) if entry["brief"] and agents.HIS_WORDS in entry["brief"] else None
    topics = entry["topics"] and "topics: " + " | ".join(cut(topic, 80) for topic in entry["topics"][-3:])
    said = "; ".join(filter(None, [held.get("thread") and f"his thread {held['thread']}", words and f'his words "{cut(words, 160)}"', held.get("record") and f"record {held['record']}", topics]))
    return said or ("nothing now, resting between items" if agents.resting(entry) else "its brief")


def routes(data: dict) -> dict[str, str]:
    """The routes his message may take, the fail-safe first, since Jev leans toward the first option: a fresh handler, then the communicator's answer, the coordinators while the drive holds a communicator whose words they hear, and each live handler."""
    found = {NEW: MEANINGS[NEW], COMMUNICATOR: MEANINGS[COMMUNICATOR]}
    if "communicator" in data["roles"]:
        found |= {COORDINATOR: MEANINGS[COORDINATOR]} | seats(data)
    return found | handlers(data)


def request(words: str, rows: str) -> dict:
    """Jev's state for the route, by CONTRACT alone: his words and the thread's newest ROWS rows; the routes ride as the Pick's options."""
    lines = [line for line in rows.splitlines() if line.strip()]
    newest = list(enumerate(lines, 1))[-ROWS:]
    return {"message": {"words": cut(words, WORDS_CHARS)}, "thread": {str(number): cut(line, ROW_CHARS) for number, line in newest}}


def picked(data: dict, words: str, rows: str) -> tuple[str, str, bool, float]:
    """The route Jev picks for his message and why, whether his words say he is going away, and how many seconds the call took; on no reading, the fail-safe fresh handler and why."""
    options = routes(data)
    began = time.monotonic()
    try:
        read = settled.jev(request(words, rows), {"route": settled.Pick(ASK, options), "away": AWAY})
    except settled.Unheard as failure:
        return NEW, f"Jev gave no reading, so a fresh handler takes it: {failure}", False, time.monotonic() - began
    route = str(read["route"])
    return route, f"Jev picked {route}: {cut(options[route], 160)}", settled.reading(float(read["away"])) is True, time.monotonic() - began


def routed_by(name: str, key: str) -> dict:
    """His earlier message's routed row once its route is taken, waiting up to ROUTED_WAIT while the call routing it still runs, so a follow-up sent while the first is still being routed goes where the first goes; the fail-safe fresh handler once the wait ends."""
    deadline = time.monotonic() + ROUTED_WAIT
    while not (kept := state.load(name)["routed"][key])["route"] and time.monotonic() < deadline:
        time.sleep(1)
    return kept if kept["route"] else kept | {"route": NEW}


def owed_ts(data: dict) -> set[str]:
    """The ts of each message of his cc-slack still owes an answer."""
    from drive import slack

    return {reply["ts"] for reply in slack.owed(agents.beside(data)[1]["session"])}


def followed(data: dict, ts: str, thread: str, words: str) -> tuple[str, str, bool, float] | None:
    """The route of his message just before this one in the same thread, while that one still waits on a real reply, cc-slack owing it, and Jev reads this one as its follow-up, with why, so it goes where the first went and one reply covers both; None otherwise."""
    earlier = [(kept["at"], key, kept) for key, kept in data["routed"].items() if key != ts and float(key) < float(ts) and kept.get("thread") == thread and state.now() - state.parse_time(kept["at"]) <= FOLLOW_WINDOW]
    if not earlier:
        return None
    _, key, kept = max(earlier)
    if key not in owed_ts(data):
        return None
    began = time.monotonic()
    try:
        follows = settled.reading(float(settled.jev({"message": {"words": cut(words, WORDS_CHARS)}, "earlier": {"words": cut(kept["words"], WORDS_CHARS)}}, {"follows": FOLLOWS})["follows"]))
    except settled.Unheard:
        return None
    if not follows:
        return None
    kept = routed_by(data["name"], key)
    return (f"{HANDLER}{kept['handler']}" if kept.get("handler") else kept["route"]), f"Jev read it as a follow-up to his message {key}, still unanswered, so it goes where that one went", False, time.monotonic() - began


def held_route(data: dict, brief: str, words: str) -> tuple[str, str, bool, float] | None:
    """The pool handler that already holds his message's thread, card or order in the routes table, else the one whose topics hold at least SIMILAR of his words' content tokens, with why, so neither costs a Jev call; None for a message neither finds."""
    if handler := agents.sticky(data, agents.route_keys(data, {}, agents.threaded(data, agents.holds(brief)))):
        return f"{HANDLER}{handler}", f"the routes table holds his thread for handler {handler}", False, 0.0
    if (match := agents.similar(data, words)) and match[1] >= SIMILAR:
        return f"{HANDLER}{match[0]}", f"{round(match[1] * 100)}% of his words' content tokens are in handler {match[0]}'s topics", False, 0.0
    return None


def route(name: str, brief: str, answer: str | None = None, away: bool = False) -> dict:
    """Route his message, the one a brief carrying his words names by its Link, once, whichever of the communicator's handler call and the watch reaches it first, by its row in the drive's routed collection: the drive hears him, Jev picks its route, the drive records the route and why with how long each step took, and the route takes it; a message routed already returns that first route."""
    link = agents.LINK.search(brief)[1]
    channel, ts, thread = state.slack_message(link)
    words = agents.his_words(brief)
    began = time.monotonic()
    with state.edit(name) as data:
        if (earlier := data["routed"].get(ts)) and not lost_handler(data, earlier):
            return earlier | {"again": True}
        data["routed"][ts] = {"link": link, "thread": thread or ts, "words": cut(words, 500), "at": state.stamp(), "route": None}
    from drive import answer as rows_answer
    from drive import watch

    data = state.load(name)
    rows = rows_answer.thread_rows(data, channel, thread or ts)
    chosen, why, said_away, jev_seconds = followed(data, ts, thread or ts, words) or held_route(data, brief, words) or picked(data, words, rows)
    away = away or said_away
    watch.hear(name, state.now(), away)
    try:
        went = take(name, chosen, brief, link, words, thread or ts, answer)
    except Exception as error:
        went = on_it(name, link, words, thread or ts) | {"fell": f"the {chosen} route raised: {cut(str(error), 300)}"}
    found = {"link": link, "thread": thread or ts, "words": cut(words, 500), "at": state.stamp(), "route": chosen, "why": why, "away": away, "jev_ms": round(jev_seconds * 1000), "ms": round((time.monotonic() - began) * 1000)} | went
    with state.edit(name) as data:
        data["routed"][ts] = found
        data["routed"] = {key: kept for key, kept in data["routed"].items() if state.now() - state.parse_time(kept["at"]) <= KEPT}
    with (state.drive_dir(name) / LOG).open("a") as out:
        out.write(json.dumps({"ts": ts} | found) + "\n")
    return found



def lost_handler(data: dict, earlier: dict) -> bool:
    """Whether the handler an earlier route of his message went to is gone, forgotten or its launch never started, so a rerun of that message routes it again rather than handing it to a handler that will never take it."""
    return bool(handler := earlier.get("handler")) and (handler not in data["handlers"] or agents.gone(data["handlers"][handler]))

def take(name: str, chosen: str, brief: str, link: str, words: str, thread: str, answer: str | None) -> dict:
    """Hand his message to the route picked: the communicator's answer from the rows, an owner record to the coordinator or to the seat working the order, as the communicator's record tool sends it, or a handler, the one picked or any; a route that cannot take it, the answer escalating or the record refused, goes to a handler with why."""
    from drive import answer as rows_answer

    if chosen == COMMUNICATOR:
        found = rows_answer.answer(name, link, words)
        if found["answered"]:
            return {"answered": found["answered"]}
        return handled(name, brief, None, answer) | {"fell": f"the communicator's answer escalated: {found['why']}"}
    if chosen == COORDINATOR or chosen.startswith(SEAT):
        try:
            return {"recorded": owner_record(name, link, words, thread, chosen.removeprefix(SEAT) if chosen.startswith(SEAT) else None)}
        except state.DriveError as error:
            return handled(name, brief, None, answer) | {"fell": f"the owner record was refused: {cut(str(error), 300)}"}
    return handled(name, brief, chosen.removeprefix(HANDLER) if chosen.startswith(HANDLER) and chosen != NEW else None, answer)


def on_it(name: str, link: str, words: str, thread: str) -> dict:
    """His message's last fallback, so nothing he sends ends silently: the communicator's answer from the rows, else a line in his thread saying the drive is on it, which names no message as answered, so cc-slack still owes it and the watch hands it to a handler."""
    from drive import answer as rows_answer
    from drive import talk

    found = rows_answer.answer(name, link, words)
    if found["answered"]:
        return {"answered": found["answered"]}
    return {"on_it": talk.posted(name, {"text": ON_IT, "thread_ts": thread})["permalink"]}


def handled(name: str, brief: str, prefer: str | None, answer: str | None) -> dict:
    """Give the brief to a handler, by agents.handle, which hands it to the live one holding its thread or the one picked."""
    from drive import cli

    briefs = cli.briefing(name, brief, answer) is not None
    handler_id, handed = agents.handle(name, brief, detached=True, answer=answer, briefs=briefs, prefer=prefer)
    return {"handler": handler_id, "handed": handed}


def owner_record(name: str, link: str, words: str, thread: str, order_id: str | None) -> str:
    """Send his words as the communicator's owner record about his work, to the coordinator session hearing his thread's newest order, or to the seat working the order named; the record tool's line."""
    from drive import tools

    item = {"kind": "owner", "text": words, "url": link, "thread": thread, "about": state.ABOUT_HIS_WORK} | ({"attrs": {"order": order_id}} if order_id else {})
    with communicating(name):
        return tools.record(name, [item], False)


@contextmanager
def communicating(name: str):
    """Run the body as the drive's communicator session, so the record tool posts from the communicator's lane as its own call would."""
    was = os.environ.get("CLAUDE_CODE_SESSION_ID")
    os.environ["CLAUDE_CODE_SESSION_ID"] = state.load(name)["roles"]["communicator"]["session"]
    try:
        yield
    finally:
        if was is None:
            del os.environ["CLAUDE_CODE_SESSION_ID"]
        else:
            os.environ["CLAUDE_CODE_SESSION_ID"] = was
