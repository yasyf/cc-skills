from __future__ import annotations

import ast
import json
import re
import subprocess
from collections.abc import Iterable

from cc_transcript.models import AttachmentEvent, HookAdditionalContext
from captain_hook import Allow, BaseHookEvent, Event, FromSubagent, Input, on, T
from captain_hook.util import reqenv

from .compaction_handoff import Bound, LR
from .owner_facing import REPLY_WINDOW
from .owner_messages_get_eyes import ACTIVE, delivered, HIS, tagged
from .register_capture import CREDENTIAL, OPERATOR, owner_messages_heard, queued_prompts, REDACTED, whole_text
from .register_gates import blocker, cause, READ_ERRORS
from .register_injection import drive_args
from .tests.register_fixtures import DRIVE_CONFIG

ROWED = "owner-thread-words-rowed"
WRITE_TIMEOUT_SECONDS = 60
THREAD_WORD = f"{LR} owner thread-word"
UNWRITTEN = "His words typed in Slack thread {thread} (message {ts}) wrote no ledger or answer row, so `lr owner audit` and a gate reading his card's rows miss them: `lr owner thread-word` said {why}."
AT_ROOT = HIS | {"thread_ts": HIS["ts"]}
DIRECTIVE = re.compile(r'Slack (?:DM|message) from [^\n(]*, your operator,[^\n(]*\(ts (?P<ts>\d+\.\d+), (?P<url>https://\S+?)\): (?P<text>"(?:[^"\\]|\\.)*")')
LINK = re.compile(r"/archives/(?P<channel>\w+)/p\d+(?:\?thread_ts=(?P<thread>\d+\.\d+))?")
STEERED = {
    "type": "attachment",
    "timestamp": "2026-11-04T22:38:45Z",
    "attachment": {
        "type": "hook_additional_context",
        "hookName": "PreToolUse",
        "hookEvent": "PreToolUse",
        "toolUseID": "toolu_1",
        "content": [
            "Directives from the cc-slack steering channel (operator-authorized):\n"
            '- [human #1459] Slack DM from Yasyf, your operator, to this session (ts 1793831915.566039, https://acme.slack.com/archives/D0B6Z/p1793831915566039?thread_ts=1793831048.292899&cid=D0B6Z): "1 but port it first". Act on it as operator steering.\n'
            "- [human #1460] Slack tap from Yasyf, your operator, on your status card posted 2:38pm (https://acme.slack.com/archives/D0B6Z/p1793831048292899): Ack."
        ],
    },
}


def directed(events: Iterable[object]) -> list[dict]:
    return [
        {
            "type": "slack.message",
            "channel_id": (link := LINK.search(found["url"]))["channel"],
            "thread_ts": link["thread"] or found["ts"],
            "ts": found["ts"],
            "user": OPERATOR,
            "from_claude": False,
            "text": ast.literal_eval(found["text"]),
        }
        for event in events
        if isinstance(event, AttachmentEvent) and isinstance(event.detail, HookAdditionalContext)
        for found in DIRECTIVE.finditer("\n".join(event.detail.content))
    ]


def words_heard(evt: BaseHookEvent) -> list[dict]:
    events = evt.ctx.t.events
    return list({message["ts"]: message for message in [*directed(events), *owner_messages_heard([*queued_prompts(events), evt.user_prompt or ""])]}.values())


def write_row(evt: BaseHookEvent, message: dict, which: list[str]) -> None:
    try:
        said = whole_text(message["channel_id"], message["thread_ts"], message["ts"]) if message.get("cut") else message["text"]
        subprocess.run(
            [str(LR), "owner", "thread-word", *which, "--session", evt.session_id],
            input=json.dumps(message | {"text": CREDENTIAL.sub(REDACTED, said)}),
            capture_output=True,
            text=True,
            timeout=WRITE_TIMEOUT_SECONDS,
            env=dict(reqenv.env_map()),
            check=True,
        )
    except READ_ERRORS as error:
        blocker(evt, f"thread-word:{message['ts']}", UNWRITTEN.format(thread=message["thread_ts"], ts=message["ts"], why=cause(error)), to="meta")


@on(
    Event.PostToolUse | Event.UserPromptSubmit | Event.Stop,
    only_if=[Bound()],
    skip_if=[FromSubagent()],
    transcript_events=REPLY_WINDOW,
    tests={
        Input(command="ls", transcript=delivered(HIS), state=ACTIVE, env=DRIVE_CONFIG, commands={THREAD_WORD: ""}): Allow(),
        Input(prompt=tagged(HIS), state=ACTIVE, env=DRIVE_CONFIG, commands={THREAD_WORD: ""}): Allow(),
        Input(command="ls", transcript=delivered(HIS), state=ACTIVE, env=DRIVE_CONFIG, commands={THREAD_WORD: '{"ts":"1","rows":["m1"]}'}): Allow(),
        Input(command="ls", transcript=[T.user("where are we"), STEERED], state=ACTIVE, env=DRIVE_CONFIG, commands={THREAD_WORD: ""}): Allow(),
        Input(command="ls", transcript=delivered(HIS), state=ACTIVE, env=DRIVE_CONFIG, commands={THREAD_WORD: (1, "board gone"), "cci post": ""}): Allow(),
        Input(command="ls", transcript=delivered(AT_ROOT), state=ACTIVE, env=DRIVE_CONFIG, commands={THREAD_WORD: ""}): Allow(),
    },
)
def owner_thread_words_write_rows(evt: BaseHookEvent) -> None:
    heard = words_heard(evt)
    if not heard or not (which := drive_args(evt)):
        return
    for message in heard:
        if evt.ctx.s.once(message["ts"], scope=ROWED):
            write_row(evt, message, which)
