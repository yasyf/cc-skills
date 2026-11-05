from __future__ import annotations

import json
from itertools import pairwise

from captain_hook import Allow, BaseHookEvent, Block, Event, HookResult, Input, Tool, on

from .board_text_gate import blocks, published
from .call_dir import located
from .cci_mcp_posts import POST_TOOL
from .compaction_handoff import Bound
from .meta_records import post_records
from .owner_facing import SLACK_CARD, Reads, fenced, slack_card
from .register_injection import INCIDENT_NAME, drive_args
from .tests.register_fixtures import DRIVE_CONFIG
from .urgent_lines_name_the_harm import LR, WHOSE_STUB, person, theirs

HELD = frozenset({"hold", "card"})
CARD_TOPIC = "card-"
REFUSAL = (
    "A hold, card or board row is for a PR a lane of this drive opened or the owner tapped. Each PR below is another person's:"
    " leave it alone, or state the fact plainly to the lane that needs it, with no hold, card or urgency."
)


def held_posts(evt: BaseHookEvent) -> list[str]:
    return [post.text for post in post_records(evt) if not INCIDENT_NAME.match(post.lane) and (post.kind.lower() in HELD or post.topic.startswith(CARD_TOPIC))]


def drawn_rows(evt: BaseHookEvent, reads: Reads) -> list[str]:
    found = []
    for call in evt.command.calls("lr"):
        if call.args[:1] != ("ask-owner",):
            continue
        source = next((arg for flag, arg in pairwise(call.args) if flag == "--card" and arg != "-"), None)
        found.append(str(evt._tool_input.get("command") or ""))
        if source and (text := reads.read(f"the board row {source}", lambda source=source, call=call: located(evt, call, source).read_text())):
            found.append(text)
    return found


def pushed_cards(evt: BaseHookEvent, reads: Reads) -> list[str]:
    pushed = [json.dumps(block) for item in published(evt, reads, []) for block, _ in blocks(item.doc) if block["type"] == "card"] if evt.tool_name == "Bash" else []
    return [*pushed, json.dumps(card)] if (card := reads.read("the status card", lambda: slack_card(evt))) else pushed


def held_lines(evt: BaseHookEvent, reads: Reads) -> list[str]:
    return [*held_posts(evt), *(drawn_rows(evt, reads) if evt.tool_name == "Bash" else []), *pushed_cards(evt, reads)]


HOLD = "cci post --kind hold --to go-sweep,landing-desk-3 --pr 46018 --text 'Hold until 12414 settles its column drop.'"
CARD = "cci post --kind report --to root --topic card-46018-column-drop --text 'Card drawn: approve the column drop.'"
REPORT_NAMING_CARD = "cci post --kind report --to meta-desk --text 'card-expired-grant-renewal, his pick: #46018 by Andrew landed and put one timer on each machine.'"
OWN_HOLD = "cci post --kind hold --to go-sweep --pr 49336 --text 'Hold until 12414 settles.'"
DRAW = "lr ask-owner --lane release-6 --until at:3d --card - <<'EOF'\n{\"type\": \"card\", \"id\": \"card-46018-column-drop\", \"title\": \"Approve Andrew's #46018 column drop?\"}\nEOF"
PUSH = "cc-present update-block - <<'EOF'\n" + json.dumps({"type": "card", "id": "card-46018-column-drop", "title": "Approve the column drop, #46018?"}) + "\nEOF"


@on(  # capt-hook: no-escape the owner ordered a hold, card or board row on another person's PR refused at post time
    Event.PreToolUse,
    only_if=[Bound(), Tool("Bash", SLACK_CARD, POST_TOOL)],
    tests={
        Input(command=HOLD, env=DRIVE_CONFIG, commands=WHOSE_STUB): Block(pattern=r"^A hold, card or board row is for a PR[\s\S]*#46018 is Andrew Benton \(andrew\)'s PR"),
        Input(command=CARD, env=DRIVE_CONFIG, commands=WHOSE_STUB): Block(pattern=r"#46018 is Andrew"),
        Input(command=DRAW, env=DRIVE_CONFIG, commands=WHOSE_STUB): Block(pattern=r"#46018 is Andrew"),
        Input(tool=POST_TOOL, tool_input={"kind": "report", "to": ["root"], "topic": "card-46018-column-drop", "text": "Card drawn for #46018."}, env=DRIVE_CONFIG, commands=WHOSE_STUB): Block(pattern=r"#46018 is Andrew"),
        Input(command=REPORT_NAMING_CARD, env=DRIVE_CONFIG, commands=WHOSE_STUB): Allow(),
        Input(command="lr ask-owner --title 'Approve the column drop, #46018?' --until at:3d", env=DRIVE_CONFIG, commands=WHOSE_STUB): Block(pattern=r"#46018 is Andrew"),
        Input(command="lr ask-owner --withdraw card-chat-limits", env=DRIVE_CONFIG, commands=WHOSE_STUB): Allow(),
        Input(command="lr ask-owner --port-of 46018 --title 'Land #49336?' --option 'Land :: It differs from #46018 in two places.'", env=DRIVE_CONFIG, commands=WHOSE_STUB): Allow(),
        Input(command="lr ask-owner --title 'port of #46018: land #49336?' --until at:3d", env=DRIVE_CONFIG, commands=WHOSE_STUB): Allow(),
        Input(command="lr ask-owner --port-of 49999 --title 'Land #49336?' --option 'Land :: It differs from #46018 in two places.'", env=DRIVE_CONFIG, commands=WHOSE_STUB): Block(pattern=r"#46018 is Andrew"),
        Input(command=PUSH, env=DRIVE_CONFIG, commands=WHOSE_STUB): Block(pattern=r"#46018 is Andrew"),
        Input(tool=SLACK_CARD, tool_input={"card": {"title": "Approve the column drop, #46018?"}}, env=DRIVE_CONFIG, commands=WHOSE_STUB): Block(pattern=r"#46018 is Andrew"),
        Input(command=HOLD.replace("--pr 46018", "").replace("Hold until", "Hold Forge-AI/monorepo#46018 until"), env=DRIVE_CONFIG, commands=WHOSE_STUB): Block(pattern=r"#46018 is Andrew"),
        Input(command=HOLD.replace("--pr 46018", "").replace("Hold until", "Hold yasyf/cc-skills#46018 until"), env=DRIVE_CONFIG, commands=WHOSE_STUB): Allow(),
        Input(command=HOLD.replace("--pr 46018", "").replace("Hold until", "Hold cc-skills PR 46018 until"), env=DRIVE_CONFIG, commands=WHOSE_STUB): Allow(),
        Input(command=HOLD.replace("--pr 46018", "--pr yasyf/cc-skills#46018"), env=DRIVE_CONFIG, commands=WHOSE_STUB): Allow(),
        Input(command=HOLD.replace("--pr 46018", "--pr Forge-AI/monorepo#46018"), env=DRIVE_CONFIG, commands=WHOSE_STUB): Block(pattern=r"#46018 is Andrew"),
        Input(command=OWN_HOLD, env=DRIVE_CONFIG, commands=WHOSE_STUB): Allow(),
        Input(command=OWN_HOLD, env=DRIVE_CONFIG, commands={f"{LR} facts whose": json.dumps({"number": 49336, "author": "drive-bot", "ours": False, "foreign": False})}): Allow(),
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
