from __future__ import annotations

import json
from pathlib import Path

from captain_hook import Allow, BaseHookEvent, Block, Event, HookResult, Input, Tool, on

from .board_text_gate import blocks, published
from .cci_mcp_posts import POST_TOOL
from .compaction_handoff import Bound
from .meta_records import posts
from .owner_facing import SLACK_CARD, Reads, fenced, slack_card
from .register_injection import INCIDENT_NAME, drive_args
from .tests.register_fixtures import DRIVE_CONFIG
from .urgent_lines_name_the_harm import WHOSE_STUB, person, theirs

HELD = frozenset({"hold", "card"})
CARD_TOPIC = "card-"
REFUSAL = (
    "A hold, card or board row is for a PR a lane of this drive opened or the owner tapped. Each PR below is another person's:"
    " leave it alone, or state the fact plainly to the lane that needs it, with no hold, card or urgency."
)


def held_posts(evt: BaseHookEvent) -> list[str]:
    return [text for lane, _, kind, text, _, _ in posts(evt) if not INCIDENT_NAME.match(lane) and (kind.lower() in HELD or CARD_TOPIC in text)]


def drawn_rows(evt: BaseHookEvent, reads: Reads) -> list[str]:
    found = []
    for call in evt.command.calls("lr"):
        if call.args[:2] != ("board", "draw"):
            continue
        source = next((arg for arg in call.args[2:] if not arg.startswith("-") and arg != "-"), None)
        found.append(str(evt._tool_input.get("command") or ""))
        if source and (text := reads.read(f"the board row {source}", lambda source=source, call=call: Path(str(call.cwd or evt.cwd), source).expanduser().read_text())):
            found.append(text)
    return found


def pushed_cards(evt: BaseHookEvent, reads: Reads) -> list[str]:
    pushed = [json.dumps(block) for item in published(evt, reads, []) for block, _ in blocks(item.doc) if block["type"] == "card"] if evt.tool_name == "Bash" else []
    return [*pushed, json.dumps(card)] if (card := reads.read("the status card", lambda: slack_card(evt))) else pushed


def held_lines(evt: BaseHookEvent, reads: Reads) -> list[str]:
    return [*held_posts(evt), *(drawn_rows(evt, reads) if evt.tool_name == "Bash" else []), *pushed_cards(evt, reads)]


HOLD = "cci post --kind hold --to go-sweep,landing-desk-3 --pr 46018 --text 'Hold until 12414 settles its column drop.'"
CARD = "cci post --kind report --to root --topic card-46018-column-drop --text 'Card drawn: approve the column drop.'"
OWN_HOLD = "cci post --kind hold --to go-sweep --pr 49336 --text 'Hold until 12414 settles.'"
DRAW = "lr board draw --lane release-6 --key card-46018 - <<'EOF'\n{\"type\": \"card\", \"id\": \"card-46018-column-drop\", \"title\": \"Approve Andrew's #46018 column drop?\"}\nEOF"
PUSH = "cc-present update-block - <<'EOF'\n" + json.dumps({"type": "card", "id": "card-46018-column-drop", "title": "Approve the column drop, #46018?"}) + "\nEOF"


@on(  # capt-hook: no-escape the owner ordered a hold, card or board row on another person's PR refused at post time
    Event.PreToolUse,
    only_if=[Bound(), Tool("Bash", SLACK_CARD, POST_TOOL)],
    tests={
        Input(command=HOLD, env=DRIVE_CONFIG, commands=WHOSE_STUB): Block(pattern=r"^A hold, card or board row is for a PR[\s\S]*#46018 is Andrew Benton \(andrew\)'s PR"),
        Input(command=CARD, env=DRIVE_CONFIG, commands=WHOSE_STUB): Block(pattern=r"#46018 is Andrew"),
        Input(command=DRAW, env=DRIVE_CONFIG, commands=WHOSE_STUB): Block(pattern=r"#46018 is Andrew"),
        Input(command=PUSH, env=DRIVE_CONFIG, commands=WHOSE_STUB): Block(pattern=r"#46018 is Andrew"),
        Input(tool=SLACK_CARD, tool_input={"card": {"title": "Approve the column drop, #46018?"}}, env=DRIVE_CONFIG, commands=WHOSE_STUB): Block(pattern=r"#46018 is Andrew"),
        Input(command=HOLD.replace("--pr 46018", "").replace("Hold until", "Hold Forge-AI/monorepo#46018 until"), env=DRIVE_CONFIG, commands=WHOSE_STUB): Block(pattern=r"#46018 is Andrew"),
        Input(command=HOLD.replace("--pr 46018", "").replace("Hold until", "Hold yasyf/cc-skills#46018 until"), env=DRIVE_CONFIG, commands=WHOSE_STUB): Allow(),
        Input(command=HOLD.replace("--pr 46018", "").replace("Hold until", "Hold cc-skills PR 46018 until"), env=DRIVE_CONFIG, commands=WHOSE_STUB): Allow(),
        Input(command=OWN_HOLD, env=DRIVE_CONFIG, commands=WHOSE_STUB): Allow(),
        Input(command="cci post --kind report --to root --text \"not ours, Andrew's #46018 resolves #382199\"", env=DRIVE_CONFIG, commands=WHOSE_STUB): Allow(),
        Input(command="cci post --kind report --to release --text \"12414 is Andrew's release and ran his #46018; no action\"", env=DRIVE_CONFIG, commands=WHOSE_STUB): Allow(),
        Input(command=HOLD.replace("cci post", "cci post --lane incident-sofi-1"), env=DRIVE_CONFIG, commands=WHOSE_STUB): Allow(),
        Input(command=HOLD): Allow(),
    },
)
def not_ours_holds(evt: BaseHookEvent) -> HookResult | None:
    if not (which := drive_args(evt)):
        return None
    reads = Reads(evt, which)
    if found := [f"#{row['number']} is {person(row)}'s PR: \"{text[:160]}\"" for text, row in theirs(reads, held_lines(evt, reads))]:
        return evt.block(fenced(REFUSAL, found))
    return evt.context(reads.warning("not_ours_holds")) if reads.failures else None
