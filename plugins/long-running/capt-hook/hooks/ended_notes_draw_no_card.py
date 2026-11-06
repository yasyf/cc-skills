from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterator

from captain_hook import Allow, BaseHookEvent, Block, CommandSchema, Event, FileFixture, HookResult, Input, Operand, Option, Tool, Warn, on
from captain_hook.util import reqenv

from .board_text_gate import calls, first
from .compaction_handoff import HOOK_POSTER, Bound, bus_name, drive_entry
from .owner_answers import read
from .owner_cards_name_their_subject import ASK_OWNER
from .owner_facing import CC_PRESENT, Reads, board_bytes, fenced
from .register_injection import DRIVE_ENV, LANE_ENV
from .settled_questions import cards
from .tests.register_fixtures import DRIVE_CONFIG

ENDED_BY_NO = "He answered No to a card asking the question below, and a No ends that note whatever ships later. Drop the draft and post nothing to them; his own new words naming the post are the approval and need no card."
PULLED = "A card you pulled asked the question below, and a pulled card is never drawn again under any id. Drop the note; to change an open card, run `cc-present ask` on its own id while it is still up."
RECORDED = "Warning, nothing was blocked: a card pulled before he answered is never drawn again, so the note below is ended for every lane of this drive. Post nothing on it and draw no card on it again."
HIS_NO = re.compile(r"^picked “No\b")
NOT_CARDS = ("#", "merge:")
LR_WITHDRAW = CommandSchema("lr", operands=(Operand("words", count="*"),), options=tuple(Option(name, (f"--{name}",)) for name in ("drive", "lane", "session", "why")))
LANE = DRIVE_CONFIG | {LANE_ENV: "bugs-comms-3"}
SESSION = "6d1e0a42"
ASKED = "Post this in the #chat-next-bug-bash thread Ria started?"
ANSWERS = "ccn -R /repo answer list"
PULLS = "cci grep"


def asking(title: str) -> str:
    return " ".join(title.split()).casefold()


def note_topic(title: str) -> str:
    return "note:" + hashlib.sha256(asking(title).encode()).hexdigest()[:12]


def answer(title: str, pick: str, at: str) -> dict:
    return {"title": title, "body": f"picked “{pick}”\nBoard question: {title}", "tags": ["board-answer", "from:owner"], "updated_at": at}


def answers(*given: dict) -> str:
    return json.dumps(list(given))


def pulled_record(title: str) -> str:
    return json.dumps({"seq": 518300, "kind": "withdraw", "topic": note_topic(title), "text": f"Pulled card slack-ria-old-chat-fixed-3530 before he answered: {title}"}) + "\n"


def on_board(card_id: str, title: str = ASKED, answered: bool = False) -> str:
    return json.dumps({"onBoard": not answered, "answers": [{"type": "choice.selected"}] if answered else [], "doc": {"blocks": [{"id": card_id, "type": "card", "title": title}]}})


def pulled_from_board(card_id: str, title: str = ASKED) -> str:
    return json.dumps({"onBoard": False, "answers": [], "doc": {"blocks": [{"id": card_id, "type": "card", "title": title}]}})


def card(card_id: str, title: str = ASKED) -> dict:
    return {"id": card_id, "type": "card", "title": title, "children": [{"id": f"{card_id}-answer", "type": "choice", "options": [{"id": "yes", "label": "Yes, post it"}, {"id": "no", "label": "No"}]}]}


def drawing(card_id: str, title: str = ASKED, **fields: object) -> dict:
    return {"command": f"cc-present ask ~/{card_id}.json", "file": FileFixture(home=True, name=f"{card_id}.json", content=json.dumps(card(card_id, title))), "session_id": SESSION, "env": LANE, **fields}


def drive_of(evt: BaseHookEvent) -> str | None:
    return bus_name(entry) if (entry := drive_entry(evt)) else reqenv.getenv(DRIVE_ENV)


def root_session(evt: BaseHookEvent) -> str | None:
    return next(reversed((drive_entry(evt) or {}).get("sessions") or [None]))


def ran(argv: list[str]) -> str:
    if (out := read(argv)) is None:
        raise OSError(f"`{' '.join(argv[:3])}` did not read")
    return out


def drawn(evt: BaseHookEvent, reads: Reads) -> list[tuple[str, str]]:
    found = [
        (str(shown.get("id") or ""), shown["title"])
        for call, _, source, piped in calls(evt, ("ask", "push"))
        if source
        for shown in cards(reads.read(f"the drawn card {source}", lambda call=call, source=source, piped=piped: json.loads(board_bytes(evt, call, source, piped))) or {})
        if shown.get("title")
    ]
    for call in evt.command.calls("lr"):
        values = ASK_OWNER.bind(call).values
        if first(values, "words") != "ask-owner" or values.get("withdraw"):
            continue
        found += [("", title) for title in values.get("title", ()) if title]
        for source in values.get("card", ()):
            asked = reads.read(f"the asked card {source}", lambda call=call, source=source: json.loads(board_bytes(evt, call, source, 0))) or {}
            found += [(str(asked.get("id") or ""), asked["title"])] if asked.get("title") else []
    return found


def pulls(evt: BaseHookEvent) -> Iterator[tuple[str, str | None]]:
    for call in evt.command.calls("cc-present"):
        values = CC_PRESENT.bind(call).values
        if first(values, "verb") == "remove-block" and (pulled := first(values, "words")):
            yield pulled, first(values, "session") or evt.session_id
    for call in evt.command.calls("lr"):
        asked, withdrawn = ASK_OWNER.bind(call).values, LR_WITHDRAW.bind(call).values
        if first(asked, "words") == "ask-owner" and (pulled := first(asked, "withdraw")):
            yield pulled, first(asked, "session") or root_session(evt)
        elif withdrawn.get("words", ())[:1] == ("withdraw",) and (pulled := next(iter(withdrawn["words"][1:]), None)) and not pulled.startswith(NOT_CARDS):
            yield pulled, first(withdrawn, "session") or root_session(evt)


def unanswered_title(reads: Reads, pulled: str, session: str | None, *, gone: bool) -> str | None:
    if not session or not (shown := reads.read(f"the pulled card {pulled}", lambda: json.loads(ran(["cc-present", "outcomes", "--block", pulled, "--session", session])))):
        return None
    if shown["answers"] or (gone and shown["onBoard"]):
        return None
    return next((block["title"] for block in shown["doc"]["blocks"] if block.get("id") == pulled and block.get("title")), None)


def ended_by_no(reads: Reads) -> set[str]:
    listed = reads.read("his board answers", lambda: json.loads(ran(["ccn", "-R", reads.repo, "answer", "list", "--label", "board-answer", "--label", "from:owner", "--json", "--limit", "0"])))
    newest = {asking(given["title"]): given.get("body") or "" for given in sorted(listed or [], key=lambda given: given.get("updated_at") or "")}
    return {title for title, body in newest.items() if HIS_NO.match(body)}


def pulled_topics(reads: Reads, drive: str | None, titles: list[str]) -> set[str]:
    if not drive or not titles:
        return set()
    argv = ["cci", "grep", "--drive", drive, "--kind", "withdraw", "--json", "--budget", "65536", *[flag for title in titles for flag in ("--topic", note_topic(title))], "."]
    found = reads.read("the pulled notes", lambda: ran(argv))
    return {json.loads(line)["topic"] for line in (found or "").splitlines() if line.startswith("{")}


class Draws:
    def check(self, evt: BaseHookEvent) -> bool:
        presented = (CC_PRESENT.bind(call).values for call in evt.command.calls("cc-present"))
        asked = (ASK_OWNER.bind(call).values for call in evt.command.calls("lr"))
        return any(first(values, "verb") in ("ask", "push") and not values.get("dry-run") for values in presented) or any(first(values, "words") == "ask-owner" and not values.get("withdraw") for values in asked)


class Pulls:
    def check(self, evt: BaseHookEvent) -> bool:
        return next(pulls(evt), None) is not None


@on(  # capt-hook: no-escape the owner ordered that a No or a pull ends a note, so no card on it reaches him again
    Event.PreToolUse,
    only_if=[Bound(), Tool("Bash"), Draws()],
    tests={
        Input(**drawing("slack-ria-old-chat-fixed-3530"), commands={ANSWERS: answers(answer(ASKED, "No", "2026-11-05T10:49:04Z")), PULLS: ""}): Block(pattern=r"^He answered No to a card asking the question below[\s\S]*Ria started\?"),
        Input(**drawing("slack-ria-old-chat-fixed-3531"), commands={ANSWERS: answers(), PULLS: pulled_record(ASKED)}): Block(pattern=r"^A card you pulled asked the question below"),
        Input(command=f"lr ask-owner --title '{ASKED}' --background 'Both fixes are out.' --option 'Yes :: posts it' --option 'No :: drops it' --nothing-to-build post", env=LANE, commands={ANSWERS: answers(answer(ASKED, "No", "2026-11-05T10:49:04Z")), PULLS: ""}): Block(
            pattern=r"answered No"
        ),
        Input(
            command="cc-present push ~/board.json",
            file=FileFixture(home=True, name="board.json", content=json.dumps({"blocks": [card("slack-ria-old-chat-fixed-3531")]})),
            session_id=SESSION,
            env=LANE,
            commands={ANSWERS: answers(), PULLS: pulled_record(ASKED)},
        ): Block(pattern=r"pulled"),
        Input(
            **{**drawing("slack-ria-old-chat-fixed-3531"), "command": "cc-present remove-block slack-ria-old-chat-fixed-3530 && cc-present ask ~/slack-ria-old-chat-fixed-3531.json"},
            commands={ANSWERS: answers(), PULLS: "", "cc-present outcomes --block slack-ria-old-chat-fixed-3530": on_board("slack-ria-old-chat-fixed-3530")},
        ): Block(pattern=r"pulled"),
        Input(**drawing("slack-ria-file-link-opens", "Tell Ria in her code-not-found thread that the file link opens now?"), commands={ANSWERS: answers(answer(ASKED, "No", "2026-11-05T10:49:04Z")), PULLS: ""}): Allow(),
        Input(**drawing("slack-ria-second-fix"), commands={ANSWERS: answers(answer(ASKED, "No", "2026-11-05T10:49:04Z"), answer(ASKED, "Yes, post it", "2026-11-06T01:00:00Z")), PULLS: ""}): Allow(),
        Input(**drawing("slack-ria-old-chat-fixed-3531"), commands={ANSWERS: answers(), PULLS: ""}): Allow(),
        Input(**drawing("slack-ria-old-chat-fixed-3530"), commands={ANSWERS: (1, "ccn: no store"), PULLS: ""}): Warn(pattern=r"could not read what it checks"),
        Input(command="lr ask-owner --withdraw card-ria-old-chat", env=LANE): Allow(),
        Input(**{**drawing("slack-ria-old-chat-fixed-3530"), "command": "cc-present ask ~/slack-ria-old-chat-fixed-3530.json --dry-run"}): Allow(),
        Input(**{**drawing("slack-ria-old-chat-fixed-3530"), "env": {}}): Allow(),
    },
)
def ended_notes_draw_no_card(evt: BaseHookEvent) -> HookResult | None:
    reads = Reads(evt, [])
    titles = [title for _, title in drawn(evt, reads)]
    if not titles:
        return None
    said_no = ended_by_no(reads)
    pulled_now = {asking(title) for pulled, session in pulls(evt) if (title := unanswered_title(reads, pulled, session, gone=False))}
    topics = pulled_topics(reads, drive_of(evt), titles)
    if refused := [title for title in titles if asking(title) in said_no]:
        return evt.block(fenced(ENDED_BY_NO, refused))
    if refused := [title for title in titles if asking(title) in pulled_now or note_topic(title) in topics]:
        return evt.block(fenced(PULLED, refused))
    return evt.context(reads.warning("ended_notes_draw_no_card")) if reads.failures else None


@on(
    Event.PostToolUse,
    only_if=[Bound(), Tool("Bash"), Pulls()],
    tests={
        Input(command="cc-present remove-block slack-ria-old-chat-fixed-3530", session_id=SESSION, env=LANE, commands={"cc-present outcomes --block": pulled_from_board("slack-ria-old-chat-fixed-3530"), "cci post": "#518300\n"}): Warn(
            pattern=r"never drawn again[\s\S]*Ria started\?"
        ),
        Input(command="lr ask-owner --withdraw card-ria-old-chat --session 1b6d1a07", env=LANE, commands={"cc-present outcomes --block": pulled_from_board("card-ria-old-chat"), "cci post": "#518301\n"}): Warn(pattern=r"never drawn again"),
        Input(command="cc-present remove-block slack-ria-file-link-opens", session_id=SESSION, env=LANE, commands={"cc-present outcomes --block": on_board("slack-ria-file-link-opens", answered=True)}): Allow(),
        Input(command="lr withdraw '#51272'", env=LANE): Allow(),
        Input(command="cc-present remove-block slack-ria-old-chat-fixed-3530", session_id=SESSION, env=LANE, commands={"cc-present outcomes --block": (1, "cc-present: daemon not running")}): Warn(pattern=r"could not read what it checks"),
    },
)
def pulled_notes_stay_ended(evt: BaseHookEvent) -> HookResult | None:
    if not (drive := drive_of(evt)):
        return None
    reads = Reads(evt, [])
    lane = reqenv.getenv(LANE_ENV) or HOOK_POSTER
    ended = []
    for pulled, session in pulls(evt):
        if not (title := unanswered_title(reads, pulled, session, gone=True)):
            continue
        argv = ["cci", "post", "--drive", drive, "--lane", lane, "--kind", "withdraw", "--to", "root", "--topic", note_topic(title), "--id", f"pulled:{pulled}", "--text", f"Pulled card {pulled} before he answered: {title}"]
        if reads.read(f"the pull record for {pulled}", lambda argv=argv: ran(argv)) is not None:
            ended.append(title)
    if reads.failures:
        return evt.context(reads.warning("pulled_notes_stay_ended"))
    return evt.context(fenced(RECORDED, ended)) if ended else None
