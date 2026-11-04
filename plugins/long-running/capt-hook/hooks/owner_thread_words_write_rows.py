from __future__ import annotations

import json
import subprocess

from captain_hook import Allow, BaseHookEvent, Event, FromSubagent, Input, on
from captain_hook.util import reqenv

from .compaction_handoff import Bound, LR
from .owner_messages_get_eyes import ACTIVE, delivered, HIS, tagged
from .register_capture import CREDENTIAL, owner_slack_messages, queued_prompts, REDACTED
from .register_gates import blocker, cause
from .register_injection import drive_args
from .tests.register_fixtures import DRIVE_CONFIG

ROWED = "owner-thread-words-rowed"
WRITE_TIMEOUT_SECONDS = 60
THREAD_WORD = f"{LR} owner thread-word"
UNWRITTEN = "His words typed in Slack thread {thread} (message {ts}) wrote no answer row, so a gate reading his card's rows misses them: `lr owner thread-word` said {why}."
AT_ROOT = HIS | {"thread_ts": HIS["ts"]}


def thread_replies(evt: BaseHookEvent) -> list[dict]:
    return [
        message
        for message in owner_slack_messages([*queued_prompts(evt.ctx.turn.events), evt.user_prompt or ""])
        if message["channel_id"].startswith("D") and message["thread_ts"] != message["ts"]
    ]


def write_row(evt: BaseHookEvent, message: dict, which: list[str]) -> None:
    try:
        subprocess.run(
            [str(LR), "owner", "thread-word", *which, "--session", evt.session_id],
            input=json.dumps(message | {"text": CREDENTIAL.sub(REDACTED, message["text"])}),
            capture_output=True,
            text=True,
            timeout=WRITE_TIMEOUT_SECONDS,
            env=dict(reqenv.env_map()),
            check=True,
        )
    except (OSError, subprocess.SubprocessError) as error:
        blocker(evt, f"thread-word:{message['ts']}", UNWRITTEN.format(thread=message["thread_ts"], ts=message["ts"], why=cause(error)), to="meta")


@on(
    Event.PostToolUse | Event.UserPromptSubmit | Event.Stop,
    only_if=[Bound()],
    skip_if=[FromSubagent()],
    tests={
        Input(command="ls", transcript=delivered(HIS), state=ACTIVE, env=DRIVE_CONFIG, commands={THREAD_WORD: ""}): Allow(),
        Input(prompt=tagged(HIS), state=ACTIVE, env=DRIVE_CONFIG, commands={THREAD_WORD: ""}): Allow(),
        Input(command="ls", transcript=delivered(HIS), state=ACTIVE, env=DRIVE_CONFIG, commands={THREAD_WORD: (1, "board gone"), "cci post": ""}): Allow(),
        Input(command="ls", transcript=delivered(AT_ROOT), state=ACTIVE, env=DRIVE_CONFIG): Allow(),
    },
)
def owner_thread_words_write_rows(evt: BaseHookEvent) -> None:
    replies = thread_replies(evt)
    if not replies or not (which := drive_args(evt)):
        return
    for message in replies:
        if evt.ctx.s.once(message["ts"], scope=ROWED):
            write_row(evt, message, which)
