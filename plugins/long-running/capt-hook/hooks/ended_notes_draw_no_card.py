from __future__ import annotations

import json
import re
from collections.abc import Iterator

from captain_hook import Allow, BaseHookEvent, Block, Event, FileFixture, HookResult, Input, Tool, Warn, WorkflowState, on, workflow_state

from .board_text_gate import calls, first
from .compaction_handoff import Bound
from .owner_answers import read
from .owner_facing import CC_PRESENT, Reads, board_bytes, fenced
from .settled_questions import cards

ENDED_BY_NO = "He answered No to a card asking the question below, and a No ends that note whatever ships later. Drop the draft and post nothing to them; his own new words naming the post are the approval and need no card."
PULLED = "A card you pulled asked the question below, and a pulled card is never drawn again under any id. Drop the note; to change an open card, run `cc-present ask` on its own id while it is still up."
NO = re.compile(r"^no\b", re.IGNORECASE)
LANE = {"CLAUDE_LONG_RUNNING_LANE": "bugs-comms-2"}
SESSION = "59e75579"
ASKED = "Post this in the #chat-next-bug-bash thread Ria started?"
BOARD = f"cc-present outcomes --all --session {SESSION}"
GONE = f"cc-present outcomes --no-doc --all --session {SESSION}"


@workflow_state("long_running_ended_notes")
class DrawnCards(WorkflowState):
    titles: dict[str, str] = {}
    pulled: list[str] = []


def asked(card_id: str, title: str = ASKED) -> dict:
    return {
        "id": card_id,
        "type": "card",
        "title": title,
        "children": [{"id": f"{card_id}-answer", "type": "choice", "options": [{"id": "yes", "label": "Yes, post it"}, {"id": "no", "label": "No"}]}],
    }


def board(*shown: dict, picks: dict[str, str] | None = None) -> str:
    return json.dumps({"doc": {"blocks": list(shown)}, "interactions": {"choices": {f"{card}-answer": {"optionIds": [pick]} for card, pick in (picks or {}).items()}}})


def gone(**titles: str) -> str:
    return json.dumps({"removed": {f"{card}-answer": {"top": card, "title": title} for card, title in titles.items()}})


def drawing(card_id: str, title: str = ASKED, **fields: object) -> dict:
    return {"command": f"cc-present ask ~/{card_id}.json", "file": FileFixture(home=True, name=f"{card_id}.json", content=json.dumps(asked(card_id, title))), "session_id": SESSION, "env": LANE, **fields}


def asking(title: str) -> str:
    return " ".join(title.split()).casefold()


def choices(block: dict) -> list[dict]:
    return [child for child in block.get("children") or () if child.get("type") == "choice"]


def picked_no(choice: dict, picks: dict) -> bool:
    labels = {option.get("id"): option.get("label", "") for option in choice.get("options") or ()}
    return any(option == "no" or NO.match(labels.get(option) or "") for option in (picks.get(choice["id"]) or {}).get("optionIds", ()))


def endings(shown: dict, off: dict, state: DrawnCards, pulling: list[str]) -> tuple[set[str], set[str], set[str]]:
    picks = shown["interactions"]["choices"]
    shown_cards = {block["id"]: block for block in shown["doc"]["blocks"] if block.get("type") == "card"}
    up = shown_cards.keys() - set(pulling)
    answered = {entry["top"] for entry in off.values()} | {card for card, block in shown_cards.items() if any(choice["id"] in picks for choice in choices(block))}
    said_no = {asking(block["title"]) for block in shown_cards.values() if any(picked_no(choice, picks) for choice in choices(block))}
    said_no |= {asking(entry["title"]) for choice, entry in off.items() if picked_no({"id": choice}, picks)}
    taken_down = {asking(state.titles[card]) for card in dict.fromkeys([*state.pulled, *pulling]) if card in state.titles and card not in up and card not in answered}
    return up, said_no, taken_down


def pulled_ids(evt: BaseHookEvent) -> Iterator[str]:
    for call in evt.command.calls("cc-present"):
        values = CC_PRESENT.bind(call).values
        if first(values, "verb") == "remove-block" and (card := next(iter(values.get("words", ())), None)):
            yield card


def ran(argv: list[str]) -> dict:
    if (out := read(argv)) is None:
        raise OSError(f"`{' '.join(argv[:2])}` did not read")
    return json.loads(out)


class DrawsOrPulls:
    def check(self, evt: BaseHookEvent) -> bool:
        return any(first(values, "verb") in ("ask", "remove-block") and not values.get("dry-run") for values in (CC_PRESENT.bind(call).values for call in evt.command.calls("cc-present")))


@on(  # capt-hook: no-escape the owner ordered that a No or a pull ends a note, so no card on it reaches him again
    Event.PreToolUse,
    only_if=[Bound(), Tool("Bash"), DrawsOrPulls()],
    tests={
        Input(**drawing("slack-ria-old-chat-fixed-3530"), commands={BOARD: board(asked("slack-ria-old-chat-correction-2"), picks={"slack-ria-old-chat-correction-2": "no"}), GONE: gone()}): Block(
            pattern=r"^He answered No to a card asking the question below[\s\S]*Ria started\?"
        ),
        Input(**drawing("slack-ria-old-chat-fixed-3531", state=[DrawnCards(titles={"slack-ria-old-chat-fixed-3530": ASKED}, pulled=["slack-ria-old-chat-fixed-3530"])]), commands={BOARD: board(), GONE: gone()}): Block(
            pattern=r"^A card you pulled asked the question below"
        ),
        Input(
            **{**drawing("slack-ria-old-chat-fixed-3531", state=[DrawnCards(titles={"slack-ria-old-chat-fixed-3530": ASKED})]), "command": "cc-present remove-block slack-ria-old-chat-fixed-3530 && cc-present ask ~/slack-ria-old-chat-fixed-3531.json"},
            commands={BOARD: board(asked("slack-ria-old-chat-fixed-3530")), GONE: gone()},
        ): Block(pattern=r"pulled"),
        Input(**drawing("slack-ria-old-chat-correction-2"), commands={BOARD: board(asked("slack-ria-old-chat-correction-2"), picks={"slack-ria-old-chat-correction-2": "no"}), GONE: gone()}): Block(),
        Input(**drawing("slack-ria-old-chat-gone-2"), commands={BOARD: board(picks={"slack-ria-old-chat-gone": "no"}), GONE: gone(**{"slack-ria-old-chat-gone": ASKED})}): Block(pattern=r"answered No"),
        Input(**drawing("slack-ria-file-link-opens", "Tell Ria in her code-not-found thread that the file link opens now?"), commands={BOARD: board(asked("slack-ria-old-chat-correction-2"), picks={"slack-ria-old-chat-correction-2": "no"}), GONE: gone()}): Allow(),
        Input(**drawing("slack-ria-old-chat-fixed-3531"), commands={BOARD: board(asked("slack-ria-old-chat-fixed-3531")), GONE: gone()}): Allow(),
        Input(**drawing("slack-ria-second-fix"), commands={BOARD: board(picks={"slack-ria-first-fix": "yes"}), GONE: gone(**{"slack-ria-first-fix": ASKED})}): Allow(),
        Input(
            **{**drawing("slack-ria-second-fix", state=[DrawnCards(titles={"slack-ria-first-fix": ASKED})]), "command": "cc-present remove-block slack-ria-first-fix && cc-present ask ~/slack-ria-second-fix.json"},
            commands={BOARD: board(asked("slack-ria-first-fix"), picks={"slack-ria-first-fix": "yes"}), GONE: gone()},
        ): Allow(),
        Input(**drawing("slack-ria-retry", state=[DrawnCards(titles={"slack-ria-retry": ASKED})]), commands={BOARD: board(), GONE: gone()}): Allow(),
        Input(**drawing("slack-ria-old-chat-fixed-3530"), commands={BOARD: (1, "cc-present: daemon not running")}): Warn(pattern=r"could not read what it checks"),
        Input(**{**drawing("slack-ria-old-chat-fixed-3530"), "command": "cc-present ask ~/slack-ria-old-chat-fixed-3530.json --dry-run"}): Allow(),
        Input(**{**drawing("slack-ria-old-chat-fixed-3530"), "env": {}}): Allow(),
    },
)
def ended_notes_draw_no_card(evt: BaseHookEvent) -> HookResult | None:
    reads = Reads(evt, [])
    pulling = list(pulled_ids(evt))
    drawn = [
        (card["id"], card["title"], first(values, "session") or evt.session_id)
        for call, values, source, piped in calls(evt, ("ask",))
        if source
        for card in cards(reads.read(f"the asked card {source}", lambda call=call, source=source, piped=piped: json.loads(board_bytes(evt, call, source, piped))) or {})
        if card.get("id") and card.get("title")
    ]
    state = DrawnCards.load(evt)
    ended_by_no, pulled = [], []
    for session in dict.fromkeys(session for _, _, session in drawn):
        if (shown := reads.read(f"his board {session}", lambda session=session: ran(["cc-present", "outcomes", "--all", "--session", session]))) is None:
            continue
        if (off := reads.read(f"the cards gone from {session}", lambda session=session: ran(["cc-present", "outcomes", "--no-doc", "--all", "--session", session]))) is None:
            continue
        up, said_no, taken_down = endings(shown, off.get("removed") or {}, state, pulling)
        ended_by_no += [title for card, title, at in drawn if at == session and asking(title) in said_no]
        pulled += [title for card, title, at in drawn if at == session and asking(title) not in said_no and asking(title) in taken_down and card not in up]
    if ended_by_no:
        return evt.block(fenced(ENDED_BY_NO, ended_by_no))
    if pulled:
        return evt.block(fenced(PULLED, pulled))
    with DrawnCards.mutate(evt) as recorded:
        recorded.titles = {**recorded.titles, **{card: title for card, title, _ in drawn}}
        recorded.pulled = list(dict.fromkeys([*recorded.pulled, *pulling]))
    return evt.context(reads.warning("ended_notes_draw_no_card")) if reads.failures else None
