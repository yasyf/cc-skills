from __future__ import annotations

import json
import subprocess

from cc_transcript import AttachmentEvent
from captain_hook import Allow, BaseHookEvent, Block, Event, FromSubagent, HookResult, Input, T, Tool, on

from .cci_mcp_posts import POST_TOOL
from .compaction_handoff import bus_name, Communicator, communicator_tests, CompactionState, drive_entry
from .meta_records import posts
from .register_capture import OPERATOR, SLACK_MESSAGE
from .tests.register_fixtures import DRIVE_CONFIG

WHO_TIMEOUT_SECONDS = 10
PEER = "peer:"
OWNER = "1b6d1a07-5c55-4f0e-9a4e-6d0f40a25e11"
THREAD, TS = "1793772834.406509", "1793772840.118201"
FOREIGN = {"session": OWNER, "card": "guardduty", "title": "Try it once?"}
ACTIVE = [CompactionState(active=True, communicator=True)]

REFUSAL = (
    "His Slack reply answers a card another session posted, so no lane of this drive may take it."
    " Send it with `cci post --to peer:<thread_owner.session>` alone, or, when `thread_owner.ended` is set, tell him that session ended, naming the card."
)


def reply(owner: dict | None = None, thread: str = THREAD) -> list:
    payload = {"type": "slack.message", "channel_id": "D0B6Z", "thread_ts": thread, "ts": TS, "user": OPERATOR, "from_claude": False, "text": "tag the engines instead"}
    if owner:
        payload["thread_owner"] = owner
    tagged = f'<channel source="plugin:cc-slack:cc-slack" subject_id="50c3b930" type="slack.message">\n{json.dumps(payload)}\n</channel>'
    return [T.user("where are we"), {"type": "attachment", "timestamp": "2026-11-04T06:14:00Z", "attachment": {"type": "queued_command", "prompt": tagged, "commandMode": "prompt"}}]


def foreign_replies(evt: BaseHookEvent) -> list[dict]:
    messages = (
        json.loads(found["payload"])
        for event in evt.ctx.turn.events
        if isinstance(event, AttachmentEvent) and event.attachment_type == "queued_command" and event.detail.prompt and (found := SLACK_MESSAGE.search(event.detail.prompt))
    )
    found = [message for message in messages if message.get("thread_owner") and message["user"] == OPERATOR]
    entry = drive_entry(evt)
    return [message for message in found if entry and not on_drive(bus_name(entry), message["thread_owner"]["session"])]


def on_drive(drive: str, session: str) -> bool:
    try:
        who = subprocess.run(["cci", "who", "--drive", drive, "--session", session, "--all", "--json"], capture_output=True, text=True, timeout=WHO_TIMEOUT_SECONDS)
    except (OSError, subprocess.TimeoutExpired):
        return True
    return who.returncode != 0 or bool(who.stdout.strip())


def carried(evt: BaseHookEvent) -> str:
    return json.dumps(evt._tool_input) if evt.tool_name == POST_TOOL else evt.command.raw


@on(
    Event.PreToolUse,
    only_if=[Communicator(), Tool("Bash", POST_TOOL)],
    skip_if=[FromSubagent()],
    tests=communicator_tests({
        Input(command=f"cci post --lane owner-relay --kind decide --to release,root --text 'Owner 10:14 PM in DM thread {THREAD}: tag the CI engines'", transcript=reply(FOREIGN), state=ACTIVE, env=DRIVE_CONFIG, commands={"cci who": ""}): Block(pattern=r"^His Slack reply answers a card another session posted[\s\S]*`cci post --to peer:<thread_owner\.session>`"),
        Input(command=f"cci post --lane owner-relay --kind ask --to root --re slack:{TS} --text 'owner:m403 fix routing'", transcript=reply(FOREIGN), state=ACTIVE, env=DRIVE_CONFIG, commands={"cci who": ""}): Block(),
        Input(tool=POST_TOOL, tool_input={"lane": "owner-relay", "kind": "decide", "to": ["release"], "text": f"slack:{TS} is for you"}, transcript=reply(FOREIGN), state=ACTIVE, env=DRIVE_CONFIG, commands={"cci who": ""}): Block(),
        Input(command=f"cci post --lane owner-relay --kind decide --to peer:{OWNER} --text 'Owner in DM thread {THREAD}: tag the CI engines'", transcript=reply(FOREIGN), state=ACTIVE, env=DRIVE_CONFIG, commands={"cci who": ""}): Allow(),
        Input(command=f"cci post --lane owner-relay --kind decide --to release --text 'Owner in DM thread {THREAD}: tag the CI engines'", transcript=reply(FOREIGN), state=ACTIVE, env=DRIVE_CONFIG, commands={"cci who": json.dumps({"session": OWNER, "lane": "release"})}): Allow(),
        Input(command="cci post --lane owner-relay --kind ask --to root --text 'owner:m404 cut the release'", transcript=reply(FOREIGN), state=ACTIVE, env=DRIVE_CONFIG, commands={"cci who": ""}): Allow(),
        Input(command=f"cci post --lane owner-relay --kind decide --to release --text 'Owner in DM thread {THREAD}: tag the CI engines'", transcript=reply(), state=ACTIVE, env=DRIVE_CONFIG): Allow(),
        Input(command=f"cci post --lane owner-relay --kind decide --to release --text 'Owner in DM thread {THREAD}: tag the CI engines'", transcript=reply(FOREIGN), state=ACTIVE, env=DRIVE_CONFIG, agent_id="a1b2c3", commands={"cci who": ""}): Allow(),
    }),
)
def thread_replies_reach_their_card_owner(evt: BaseHookEvent) -> HookResult | None:
    recipients = [to for _, tos, *_ in posts(evt) for to in tos]
    if not recipients:
        return None
    text = carried(evt)
    for message in foreign_replies(evt):
        owner = message["thread_owner"]
        if (message["ts"] in text or message["thread_ts"] in text) and any(to != f"{PEER}{owner['session']}" for to in recipients):
            return evt.block(REFUSAL)
    return None
