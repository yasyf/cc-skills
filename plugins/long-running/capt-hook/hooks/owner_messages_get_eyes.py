from __future__ import annotations

import json

from captain_hook import Allow, BaseHookEvent, Event, FromSubagent, Input, T, on

from .compaction_handoff import Bound, CompactionState
from .owner_returns import cc_slack, SHIPPED_CC_SLACK
from .register_capture import OPERATOR, owner_slack_messages, delivered_prompts
from .tests.register_fixtures import DRIVE_CONFIG

EYES = "eyes"
SEEN = "owner-messages-eyed"
ACTIVE = [CompactionState(active=True, communicator=True)]
REACT = {f"{SHIPPED_CC_SLACK} react": ""}
HIS = {"type": "slack.message", "channel_id": "D0B6Z", "thread_ts": "1793684147.131059", "ts": "1793705742.180139", "user": OPERATOR, "from_claude": False, "text": "why no reply"}


def tagged(payload: dict) -> str:
    return f'<channel source="plugin:cc-slack:cc-slack" subject_id="50c3b930" type="slack.message">\n{json.dumps(payload)}\n</channel>'


def delivered(payload: dict) -> list:
    return [T.user("where are we"), {"type": "attachment", "timestamp": "2026-11-04T17:45:00Z", "attachment": {"type": "queued_command", "prompt": tagged(payload), "commandMode": "prompt"}}]


@on(
    Event.PostToolUse | Event.UserPromptSubmit | Event.Stop,
    only_if=[Bound()],
    skip_if=[FromSubagent()],
    tests={
        Input(command="ls", transcript=delivered(HIS), state=ACTIVE, env=DRIVE_CONFIG, commands=REACT): Allow(),
        Input(prompt=tagged(HIS), state=ACTIVE, env=DRIVE_CONFIG, commands=REACT): Allow(),
        Input(command="ls", transcript=delivered(HIS), state=ACTIVE, env=DRIVE_CONFIG, commands={f"{SHIPPED_CC_SLACK} react": (1, "channel_not_found"), "cci post": ""}): Allow(),
        Input(command="ls", transcript=[T.user("where are we")], state=ACTIVE, env=DRIVE_CONFIG): Allow(),
    },
)
def owner_messages_get_eyes(evt: BaseHookEvent) -> None:
    for message in owner_slack_messages([*delivered_prompts(evt.ctx.turn.events), evt.user_prompt or ""]):
        if evt.ctx.s.once(message["ts"], scope=SEEN):
            cc_slack(evt, "react", "--channel", message["channel_id"], "--ts", message["ts"], "--name", EYES)
