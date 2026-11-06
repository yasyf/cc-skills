from __future__ import annotations

import json
import re
import subprocess
from datetime import UTC, datetime, timedelta
from functools import partial

from captain_hook import Allow, Annotated, BaseHookEvent, Block, Event, FromSubagent, HookResult, Input, Tool, Warn, on
from captain_hook.util import reqenv

from .compaction_handoff import ROOT_LANE, Bound, Communicator, CompactionState, Coordinator, bus_name, communicator_tests, coordinator_tests, drive_entry, script_module
from .owner_facing import post_values, board_bytes, board_sources, posted_text
from .root_replies import DRIVE_ACTIVE, OWNER_WAITS, PACIFIC, owner_waits
from .tests.register_fixtures import DRIVE_CONFIG

READ_SECONDS = 30
RECORD_BYTES = 4_000_000
NOTE_WINDOW = timedelta(hours=24)
NOTE_GRACE = timedelta(minutes=10)
NOTE_CHARS = 120
BOARD_LANE = "board-watch"
FEEDBACK = "feedback.created"
ROW_PREFIX = "merge-row-"
PR_NUMBER = re.compile(r"#(\d{3,6})\b")
BOARD_ROWS = re.compile(r"\bboard rows?\b|\b(?:on|to|onto) (?:the |his |your )?board\b", re.IGNORECASE)
UNANSWERED = (
    "These owner board notes have waited over 10 minutes with no reply on the board. "
    "Answer each with `cc-present reply --block <block> --body '<answer>'`, then stop again.\n```\n{lines}\n```"
)
UNROWED = (
    "Warning, nothing was blocked: these cards carry the owner's answer with no ccn answer row, and this call takes them off the board. "
    "Run `board_watch.py backfill --session {session} --program {drive}` to write the rows board-watch missed.\n```\n{lines}\n```"
)
APPROVED = "Warning, nothing was blocked: the owner already approved these PRs at their current heads, so they need no new board row. Withdraw them from this order.\n```\n{lines}\n```"

NOT_ANSWERED = (
    "Only his tap, numbered reply or a relay's record of his words closes a card as answered. "
    "Record his words with `ccn answer add --label from:owner --label card:<card>`, or leave the card up and tell the lane that drew it.\n```\n{cards}\n```"
)
SAYS_HE_ANSWERED = re.compile(r"\b(?:you|he)(?: already)? answered\b", re.IGNORECASE)
NUMBERED_PICK = re.compile(r"^(?:(?:i )?pick |option )?#?\d+(?:\.?$|[\s,:;+-])", re.IGNORECASE)
THREAD_PROOF = "proof:slack-thread:"
WRITE_IN_WORDS = "wrote “"
ANSWER_ROWS = script_module("long_running.answer_rows")
CARD_SHOWN = "cc-present outcomes --block card-a --session s"
LIVE = "cc-present outcomes --all --no-doc --session s"
ROW_LIST = "ccn -R /repo answer list"
WRITE_IN = {"type": "choice.selected", "origin": "human", "at": "2026-11-02T04:44:04-08:00", "round": 3, "payload": {"blockId": "q1", "optionIds": [], "other": "flush them at startup", "type": "choice.selected"}}
TAPPED = {"type": "choice.selected", "origin": "human", "at": "2026-11-02T04:44:04-08:00", "round": 3, "payload": {"blockId": "q1", "optionIds": ["q1-1"], "type": "choice.selected"}}
REMOVED_AS_ANSWERED = "cc-present remove-block card-a --session s --reason 'you answered this'"
CARD_A = {"id": "card-a", "type": "card", "title": "Cache?", "children": [{"id": "q1", "type": "choice"}]}

NOW = datetime.now(UTC)
NOTE = "why is the relay slot here at all?"
ROW_NOTE = "no one said to get rid of this"
BOARD = "cc-present outcomes --all --no-doc --session"
NOTES = f"cci tail --drive brook-drive --lane {BOARD_LANE}"
ROOTS = f"cci tail --drive brook-drive --lane {ROOT_LANE}"
CHECK = f"{OWNER_WAITS} --drive brook-drive --json --pr"


def stamp(minutes_ago: float) -> str:
    return (NOW - timedelta(minutes=minutes_ago)).isoformat().replace("+00:00", "Z")


def board(feedback: dict | None = None, triage: dict | None = None, replies: dict | None = None, retired: tuple[str, ...] = ()) -> str:
    return json.dumps({"interactions": {"feedback": feedback or {}, "triage": triage or {}, "replies": replies or {}}, "retired": list(retired)})


def noted(minutes_ago: float, block: str = "card-relay", text: str = NOTE) -> str:
    return json.dumps({"seq": 7, "lane": BOARD_LANE, "kind": "owner", "at": stamp(minutes_ago), "text": f"Owner on board s, Relay card ({block}): noted “{text}”", "fields": {"attrs": {"block": block, "event": FEEDBACK}}})


def root_record(minutes_ago: float, text: str) -> str:
    return json.dumps({"seq": 9, "lane": ROOT_LANE, "kind": "answer", "at": stamp(minutes_ago), "text": text, "refs": {}})


def row_note(minutes_ago: float) -> dict:
    return {"merge-prs": {"47326": {"verdict": "rejected", "head": "770a31b", "note": ROW_NOTE, "at": stamp(minutes_ago)}}}


def waits(line: str) -> str:
    return json.dumps([{"pr": "47265", "head": "827dd7f0a1", "waiting": False, "line": line}])


def read(argv: list[str]) -> str | None:
    done = subprocess.run(argv, capture_output=True, text=True, timeout=READ_SECONDS, env=dict(reqenv.env_map()))
    return None if done.returncode else done.stdout


class Unread(Exception):
    pass


def ran(prefix: list[str], argv: list[str]) -> str:
    if (out := read([*prefix, *argv])) is None:
        raise Unread(prefix[0])
    return out


def shown_card(answers: list[dict], on_board: bool = True) -> str:
    return json.dumps({"doc": {"blocks": [CARD_A]}, "onBoard": on_board, "answers": answers})


def row_closes(row: dict) -> bool:
    said = (row.get("body") or "").split("\n", 1)[0]
    if not said.startswith(WRITE_IN_WORDS) and not any(tag.startswith(THREAD_PROOF) for tag in row.get("tags") or ()):
        return True
    words = said.removeprefix(WRITE_IN_WORDS).removesuffix("”").strip()
    return bool(NUMBERED_PICK.match(words)) and not words.endswith("?")


def answer_stands(present, ccn, session: str, card: str) -> bool:
    shown = json.loads(present(["outcomes", "--block", card, "--session", session]))
    if any(answer["origin"] == "human" and answer["payload"].get("optionIds") for answer in shown.get("answers") or []):
        return True
    return any(row_closes(row) for label in ("card:", "board-card:") for row in json.loads(ccn(["answer", "list", "--label", "from:owner", "--label", label + card, "--limit", "0", "--full", "--json"]) or "[]"))


def leaving(values: dict[str, tuple], read_board, session: str) -> list[tuple[str, set[str] | None]]:
    """Each card this cc-present call takes off the board, with the children it drops when the card stays: a remove-block's block, the top-level blocks a push leaves out, and the children an update-block of a card on the board leaves out."""
    match next(iter(values.get("verb", ())), None), values.get("words", ()):
        case "remove-block", (block, *_):
            return [(block, None)]
        case "push", (_, *_):
            live = json.loads(ran(["cc-present"], ["outcomes", "--all", "--no-doc", "--session", session]))["rounds"]["blockRounds"]
            pushed = {block["id"] for block in json.loads(read_board())["blocks"]}
            return [(top, None) for top in sorted(set(live) - pushed)]
        case "update-block", (_, *_):
            block = json.loads(read_board())
            if block.get("type") != "card":
                return []
            shown = json.loads(ran(["cc-present"], ["outcomes", "--block", block["id"], "--session", session]))
            old = (shown.get("doc") or {}).get("blocks") or [{}]
            dropped = {child["id"] for child in old[0].get("children") or []} - {child["id"] for child in block.get("children") or []}
            return [(block["id"], dropped)] if shown.get("onBoard") and dropped else []
    return []


def records(drive: str, lane: str, *extra: str) -> list[dict] | None:
    out = read(["cci", "tail", "--drive", drive, "--lane", lane, *extra, "--since", "24h", "--budget", str(RECORD_BYTES), "--json"])
    return None if out is None else [json.loads(line) for line in out.splitlines() if line.strip()]


def at(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def clock(moment: datetime) -> str:
    return moment.astimezone(PACIFIC).strftime("%-I:%M %p")


def due(moment: datetime, now: datetime) -> bool:
    return NOTE_GRACE < now - moment <= NOTE_WINDOW


def unanswered_notes(state: dict, notes: list[dict], now: datetime) -> list[str]:
    feedback, retired = state["interactions"].get("feedback") or {}, set(state.get("retired") or ())
    lines = []
    for record in notes:
        attrs = (record.get("fields") or {}).get("attrs") or {}
        if attrs.get("event") != FEEDBACK or (block := attrs.get("block", "")) in retired or not due(at(record["at"]), now):
            continue
        lines += [f"{block} at {clock(at(record['at']))}: “{note['text'][:NOTE_CHARS]}”" for note in after_newest_reply(feedback.get(block, [])) if note.get("text") and note["text"][:NOTE_CHARS] in record["text"]]
    return lines


def after_newest_reply(thread: list[dict]) -> list[dict]:
    return thread[max((i + 1 for i, note in enumerate(thread) if note.get("replies")), default=0) :]


def row_notes(state: dict, now: datetime) -> list[tuple[str, str, dict]]:
    replies, retired = state["interactions"].get("replies") or {}, set(state.get("retired") or ())
    pending = []
    for block, rows in (state["interactions"].get("triage") or {}).items():
        for row, entry in rows.items():
            pr = row.removeprefix(ROW_PREFIX)
            if block in retired or not (entry.get("note") and entry.get("at") and due(at(entry["at"]), now)) or any(mentions(reply.get("md") or "", pr) for reply in replies.get(block, [])):
                continue
            pending.append((block, pr, entry))
    return pending


def mentions(text: str, pr: str) -> bool:
    return bool(re.search(rf"\b{re.escape(pr)}\b", text))


def answered(pr: str, since: datetime, roots: list[dict]) -> bool:
    return any(at(record["at"]) > since and (mentions(record.get("text") or "", pr) or pr in {str(n) for n in (record.get("refs") or {}).get("prs") or ()}) for record in roots)


@on(
    Event.Stop,
    skip_if=[FromSubagent()],
    only_if=[Communicator()],
    tests=communicator_tests({
        Input(state=DRIVE_ACTIVE, env=DRIVE_CONFIG, commands={BOARD: board({"card-relay": [{"id": "f1", "text": NOTE}]}), NOTES: noted(30), ROOTS: ""}): Block(pattern=r"card-relay at \d{1,2}:\d{2} [AP]M: “why is the relay slot here at all\?”"),
        Input(state=DRIVE_ACTIVE, env=DRIVE_CONFIG, commands={BOARD: board({"card-relay": [{"id": "f1", "text": NOTE, "replies": [{"id": "r1", "md": "It carries the session."}]}]}), NOTES: noted(30), ROOTS: ""}): Allow(),
        Input(state=DRIVE_ACTIVE, env=DRIVE_CONFIG, commands={BOARD: board({"card-relay": [{"id": "f1", "text": NOTE}, {"id": "f2", "text": "and the slot under it?", "replies": [{"id": "r1", "md": "Both carry the session."}]}]}), NOTES: noted(30), ROOTS: ""}): Allow(),
        Input(state=DRIVE_ACTIVE, env=DRIVE_CONFIG, commands={BOARD: board({"card-relay": [{"id": "f0", "text": "and the slot under it?", "replies": [{"id": "r1", "md": "It carries the session."}]}, {"id": "f1", "text": NOTE}]}), NOTES: noted(30), ROOTS: ""}): Block(pattern=r"card-relay at .*: “why is the relay slot here at all\?”"),
        Input(state=DRIVE_ACTIVE, env=DRIVE_CONFIG, commands={BOARD: board({"card-relay": [{"id": "f1", "text": NOTE}]}), NOTES: noted(5), ROOTS: ""}): Allow(),
        Input(state=DRIVE_ACTIVE, env=DRIVE_CONFIG, commands={BOARD: board({"card-relay": [{"id": "f1", "text": NOTE}]}, retired=("card-relay",)), NOTES: noted(30), ROOTS: ""}): Allow(),
        Input(state=DRIVE_ACTIVE, env=DRIVE_CONFIG, commands={BOARD: board(triage=row_note(30)), NOTES: "", ROOTS: ""}): Block(pattern=r"merge-prs #47326 at .*: “no one said to get rid of this”"),
        Input(state=DRIVE_ACTIVE, env=DRIVE_CONFIG, commands={BOARD: board(triage=row_note(30)), NOTES: "", ROOTS: root_record(20, "Closed PR 47326 at the owner's word")}): Allow(),
        Input(state=DRIVE_ACTIVE, env=DRIVE_CONFIG, commands={BOARD: board(triage=row_note(30), replies={"merge-prs": [{"id": "r2", "md": "#47326 is closed."}]}), NOTES: "", ROOTS: ""}): Allow(),
        Input(state=DRIVE_ACTIVE, env=DRIVE_CONFIG, commands={BOARD: (1, "no board"), NOTES: noted(30), ROOTS: ""}): Allow(),
        Input(env=DRIVE_CONFIG, commands={BOARD: board({"card-relay": [{"id": "f1", "text": NOTE}]}), NOTES: noted(30), ROOTS: ""}): Allow(),
    }),
)
def owner_notes_get_answers(evt: BaseHookEvent) -> HookResult | None:
    """The communicator stops only once every owner note on its board from the last day that is over 10 minutes old has an answer: a board note is answered by any reply on its block to it or a later note, and a merge-row note a board reply or a coordinator record naming its PR. A board or bus that does not read blocks nothing."""
    if not CompactionState.load(evt).active or not (entry := drive_entry(evt)):
        return None
    drive = bus_name(entry)
    if (shown := read(["cc-present", "outcomes", "--all", "--no-doc", "--session", evt.session_id])) is None or (notes := records(drive, BOARD_LANE, "--kind", "owner")) is None:
        return None
    state, now = json.loads(shown), datetime.now(UTC)
    lines = unanswered_notes(state, notes, now)
    if (pending := row_notes(state, now)) and (roots := records(drive, ROOT_LANE)) is not None:
        lines += [f"{block} #{pr} at {clock(at(entry['at']))}: “{entry['note'][:NOTE_CHARS]}”" for block, pr, entry in pending if not answered(pr, at(entry["at"]), roots)]
    return evt.block(UNANSWERED.format(lines="\n".join(lines))) if lines else None


@on(
    Event.PreToolUse,
    only_if=[Coordinator(), Tool("Bash")],
    skip_if=[FromSubagent(), Annotated("raw")],
    tests=coordinator_tests({
        Input(command="cci post --drive brook-drive --lane root --kind go --to board-refresher-6 --text 'put #47265 on his board'", state=DRIVE_ACTIVE, env=DRIVE_CONFIG, commands={CHECK: waits("#47265 at 827dd7f: not waiting on you: you approved it at 827dd7f at 1:46 AM")}): Warn(pattern=r"#47265 at 827dd7f: not waiting on you: you approved it"),
        Input(command="cci post --drive brook-drive --lane root --kind go --to board-refresher-6 --text 'board rows for #47265'", state=DRIVE_ACTIVE, env=DRIVE_CONFIG, commands={CHECK: waits("#47265 at 827dd7f: not waiting on you: the board holds no row for it")}): Allow(),
        Input(command="cci post --drive brook-drive --lane root --kind go --to landing-desk-3 --text 'land #47265'", state=DRIVE_ACTIVE, env=DRIVE_CONFIG, commands={CHECK: waits("#47265 at 827dd7f: not waiting on you: you approved it at 827dd7f")}): Allow(),
        Input(command="cci post --drive brook-drive --lane board-refresher-6 --kind state --text 'put #47265 on his board'", state=DRIVE_ACTIVE, env=DRIVE_CONFIG, commands={CHECK: waits("#47265 at 827dd7f: not waiting on you: you approved it at 827dd7f")}): Allow(),
        Input(command="cci post --drive brook-drive --lane root --kind go --text 'put #47265 on his board'", env=DRIVE_CONFIG, commands={CHECK: waits("#47265 at 827dd7f: not waiting on you: you approved it at 827dd7f")}): Allow(),
    }),
)
def board_rows_skip_approved_prs(evt: BaseHookEvent) -> HookResult | None:
    """A coordinator order that asks for board rows on PRs the owner already approved at their current heads warns with those PRs (owner, 2:48 AM Oct 31). A read that fails blocks nothing."""
    if not CompactionState.load(evt).active or not (entry := drive_entry(evt)):
        return None
    texts = [posted_text(values) for call in evt.command.calls("cci") if (values := post_values(evt, call)).get("words", ())[:1] == ("post",) and (values.get("lane") or (ROOT_LANE,))[0] in (ROOT_LANE, "main")]
    if not (prs := sorted({pr for text in texts if BOARD_ROWS.search(text) for pr in PR_NUMBER.findall(text)})):
        return None
    try:
        found = owner_waits(bus_name(entry), *(arg for pr in prs for arg in ("--pr", pr)))
    except (OSError, subprocess.SubprocessError, ValueError):
        return None
    lines = [item["line"] for item in found if item.get("head") and f"approved it at {item['head'][:7]}" in item["line"]]
    return evt.context(APPROVED.format(lines="\n".join(lines))) if lines else None


@on(
    Event.PreToolUse,
    only_if=[Bound(), Tool("Bash")],
    skip_if=[Annotated("raw")],
    tests={
        Input(command=REMOVED_AS_ANSWERED, env=DRIVE_CONFIG, commands={CARD_SHOWN: shown_card([]), ROW_LIST: "[]"}): Block(pattern=r"Only his tap[\s\S]*card-a"),
        Input(command=REMOVED_AS_ANSWERED, env=DRIVE_CONFIG, commands={CARD_SHOWN: shown_card([]), ROW_LIST: json.dumps([{"tags": ["from:owner", THREAD_PROOF + "1"], "body": "what does that word mean?\nBoard question: Cache?"}])}): Block(pattern=r"Only his tap"),
        Input(command=REMOVED_AS_ANSWERED, env=DRIVE_CONFIG, commands={CARD_SHOWN: shown_card([]), ROW_LIST: json.dumps([{"tags": ["from:owner", THREAD_PROOF + "1"], "body": "1. this line is not needed, 2) there is no button here\nBoard question: Cache?"}])}): Block(pattern=r"Only his tap"),
        Input(command=REMOVED_AS_ANSWERED, env=DRIVE_CONFIG, commands={CARD_SHOWN: shown_card([]), ROW_LIST: json.dumps([{"tags": ["from:owner", THREAD_PROOF + "1"], "body": "I pick 1\nBoard question: Cache?"}])}): Allow(),
        Input(command=REMOVED_AS_ANSWERED, env=DRIVE_CONFIG, commands={CARD_SHOWN: shown_card([]), ROW_LIST: json.dumps([{"tags": ["from:owner", "card:card-a"], "body": "Owner, in the card's thread: keep it."}])}): Allow(),
        Input(command=REMOVED_AS_ANSWERED, env=DRIVE_CONFIG, commands={CARD_SHOWN: shown_card([TAPPED]), ROW_LIST: "[]"}): Allow(),
        Input(command=REMOVED_AS_ANSWERED, env=DRIVE_CONFIG, commands={CARD_SHOWN: (1, "cc-present: daemon down")}): Allow(),
        Input(command="cc-present remove-block card-a --session s --reason 'being redone after review'", env=DRIVE_CONFIG, commands={CARD_SHOWN: shown_card([]), ROW_LIST: "[]"}): Allow(),
        Input(command=REMOVED_AS_ANSWERED, commands={CARD_SHOWN: shown_card([]), ROW_LIST: "[]"}): Allow(),
    },
)
def only_his_answer_closes_a_card(evt: BaseHookEvent) -> HookResult | None:
    """A hand `cc-present remove-block` whose reason says the owner answered runs only when his tap, numbered reply or a relay's record of his words stands behind the card."""
    if not (entry := drive_entry(evt)):
        return None
    ccn, present = partial(ran, ["ccn", "-R", entry["checkout"]]), partial(ran, ["cc-present"])
    unanswered = []
    try:
        for _, values, _, _ in board_sources(evt):
            says = next(iter(values.get("reason", ())), "")
            if next(iter(values.get("verb", ())), None) != "remove-block" or not SAYS_HE_ANSWERED.search(says):
                continue
            card = next(iter(values.get("words", ())))
            session = next(iter(values.get("session", ())), None) or evt.session_id
            if not answer_stands(present, ccn, session, card):
                unanswered.append(card)
    except (Unread, OSError, ValueError, KeyError, AttributeError, TypeError, StopIteration, subprocess.SubprocessError):
        return None
    return evt.block(NOT_ANSWERED.format(cards="\n".join(unanswered))) if unanswered else None


@on(
    Event.PreToolUse,
    only_if=[Bound(), Tool("Bash")],
    skip_if=[Annotated("raw")],
    tests={
        Input(command="cc-present remove-block card-a --session s", env=DRIVE_CONFIG, commands={CARD_SHOWN: shown_card([WRITE_IN]), ROW_LIST: "[]"}): Warn(pattern=r"card-a: q1 answered .*flush them at startup"),
        Input(command="cc-present remove-block card-a --session s", env=DRIVE_CONFIG, commands={CARD_SHOWN: shown_card([WRITE_IN]), ROW_LIST: json.dumps([{"tags": ["board-answer", "board-block:q1"]}])}): Allow(),
        Input(command="cc-present remove-block card-a --session s", env=DRIVE_CONFIG, commands={CARD_SHOWN: shown_card([])}): Allow(),
        Input(command="cc-present remove-block card-a --session s", env=DRIVE_CONFIG, commands={CARD_SHOWN: (1, "cc-present: daemon down")}): Allow(),
        Input(
            command="""echo '{"version":1,"title":"T","blocks":[{"id":"m1","type":"markdown","md":"next"}]}' | cc-present push - --session s""",
            env=DRIVE_CONFIG,
            commands={LIVE: json.dumps({"rounds": {"blockRounds": {"card-a": 3, "m1": 3}}}), CARD_SHOWN: shown_card([WRITE_IN]), ROW_LIST: "[]"},
        ): Warn(pattern=r"card-a: q1 answered"),
        Input(
            command="""echo '{"id":"card-a","type":"card","title":"Cache?","children":[]}' | cc-present update-block - --session s""",
            env=DRIVE_CONFIG,
            commands={CARD_SHOWN: shown_card([WRITE_IN]), ROW_LIST: "[]"},
        ): Warn(pattern=r"card-a: q1 answered"),
        Input(
            command="""echo '{"id":"card-a","type":"card","title":"Cache?","children":[{"id":"q1","type":"choice"}]}' | cc-present update-block - --session s""",
            env=DRIVE_CONFIG,
            commands={CARD_SHOWN: shown_card([WRITE_IN])},
        ): Allow(),
        Input(command="cc-present remove-block card-a --session s", commands={CARD_SHOWN: shown_card([WRITE_IN]), ROW_LIST: "[]"}): Allow(),
        Input(command="cc-present update-block --help", env=DRIVE_CONFIG): Allow(),
        Input(command="cc-present push --help", env=DRIVE_CONFIG): Allow(),
    },
)
def answered_cards_keep_their_rows(evt: BaseHookEvent) -> HookResult | None:
    """A card carrying the owner's answer leaves the board, by a remove-block, a push that leaves it out, or an update-block that drops an answered child, only once each answer has its ccn answer row (owner, 2:25 PM Nov 2: "fic the cc-present tooling so this issue doesnt happen agakn"). A read that fails passes the call."""
    if not (entry := drive_entry(evt)):
        return None
    ccn, present = partial(ran, ["ccn", "-R", entry["checkout"]]), partial(ran, ["cc-present"])
    lines, sessions = [], []
    try:
        for call, values, source, piped in board_sources(evt):
            if values.get("dry-run"):
                continue
            session = next(iter(values.get("session", ())), None) or evt.session_id
            for card, dropped in leaving(values, partial(board_bytes, evt, call, source, piped), session):
                owed = ANSWER_ROWS.unrowed(present, ccn, session, card, dropped)
                lines += [f"{card}: {answer['payload']['blockId']} answered {answer['at']}: {answer['payload'].get('other') or answer['payload'].get('text') or ', '.join(answer['payload'].get('optionIds') or ()) or answer['payload'].get('verdict') or ''}" for answer in owed]
                sessions += [session] if owed else []
    except (Unread, OSError, ValueError, KeyError, subprocess.SubprocessError):
        return None
    return evt.context(UNROWED.format(session=sessions[0], drive=bus_name(entry), lines="\n".join(lines))) if lines else None
