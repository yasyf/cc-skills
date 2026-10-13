from __future__ import annotations

import json
import re
import shlex
import subprocess
import sys
from collections.abc import Iterator
from dataclasses import dataclass
from urllib.parse import parse_qs, urlparse

from captain_hook import Allow, BaseHookEvent, Event, FromSubagent, HookResult, Input, Or, Tool, Warn, on
from captain_hook.util import reqenv

from .compaction_handoff import script
from .pr_ledger import RECORD_TIMEOUT_SECONDS, named_lane, run_drive
from .root_context import DriveActive, OwnerPrompt
from .tests.root_fixtures import ACTIVE

POST_TOOLS = (
    "mcp__plugin_cc-slack_cc-slack__slack_send",
    "mcp__plugin_cc-slack_cc-slack__slack_reply",
    "mcp__slack__slack_send_message",
)
POST_VERBS = {"send", "reply"}
CC_SLACK_REPLY = (
    '{"channel_id":"C0AAAAAAAA1","ts":"1790901997.422529","permalink":"https://example.slack.com/archives/C0AAAAAAAA1/'
    'p1790901997422529?thread_ts=1790901035.467689&cid=C0AAAAAAAA1","posted":true,"watched":false}'
)
CC_SLACK_SEND = '{"channel_id":"C0BBBBBBBB2","ts":"1790875497.353829","permalink":"https://example.slack.com/archives/C0BBBBBBBB2/p1790875497353829","posted":true}'
MCP_SENT = '{\n  "status": "sent",\n  "channel": "C0AAAAAAAA1",\n  "ts": "1790902074.413409",\n  "thread_ts": "1790901035.467689",\n  "message": "x"\n}'
THREAD = f"{sys.executable} {script('drive.py')} thread"
UNWATCHED = "The Slack thread {thread} was not added to the drive's watch list. Run `drive.py thread` for it by hand."
CHANNEL = r"(?P<channel>[CDG](?=[A-Z]*\d)[A-Z0-9]{8,})(?![A-Z0-9])"
MESSAGE = r"(?P<message>p\d{16}|\d{10}\.\d{6})(?!\d)"
LINKS = (
    re.compile(rf"https://[\w.-]+\.slack\.com/archives/{CHANNEL}(?:/{MESSAGE}(?:\?[^\s>|]*?\bthread_ts=(?P<thread>\d{{10}}\.\d{{6}}))?)?"),
    re.compile(rf"(?<![\w/]){CHANNEL}[ /]{MESSAGE}"),
)
OWNER = "owner"
WATCH = f"{sys.executable} {script('drive.py')} watch"
FENCE = "~" * 12
OWNER_LINK = "runs are broken, see https://example.slack.com/archives/C0AAAAAAAA1/p1790901997422529?thread_ts=1790901035.467689&cid=C0AAAAAAAA1"
ON_WATCH = "{thread} is on the drive's Slack watch list at state"
WATCHING = (
    "The Slack links in this prompt are on the drive's watch list, so never add them by hand. "
    "If no Slack watch lane is live, spawn one from `reference/slack-watch-brief.md`."
)
NOT_WATCHING = "A Slack link in this prompt did not reach the drive's watch list. Rerun the command below."


@dataclass(frozen=True)
class PostedThread:
    channel: str
    thread_ts: str
    posted_ts: str


def response_texts(response: object) -> list[str]:
    match response:
        case str():
            return [response]
        case {"stdout": stdout}:
            return [stdout]
        case [*blocks]:
            return [block["text"] for block in blocks if isinstance(block, dict) and "text" in block]
        case _:
            return []


def receipts(text: str) -> Iterator[dict]:
    decoder = json.JSONDecoder()
    for at in (index for index, char in enumerate(text) if char == "{"):
        try:
            found, _ = decoder.raw_decode(text, at)
        except ValueError:
            continue
        if isinstance(found, dict) and found.get("ts") and (found.get("channel_id") or found.get("channel")):
            yield found


def posted_threads(response: object) -> list[PostedThread]:
    threads = (
        PostedThread(
            channel=receipt.get("channel_id") or receipt["channel"],
            thread_ts=receipt.get("thread_ts")
            or parse_qs(urlparse(receipt.get("permalink", "")).query).get("thread_ts", [receipt["ts"]])[0],
            posted_ts=receipt["ts"],
        )
        for text in response_texts(response)
        for receipt in receipts(text)
    )
    return list(dict.fromkeys(threads))


def posts_to_slack(evt: BaseHookEvent) -> bool:
    if evt.tool_name in POST_TOOLS:
        return True
    return any(call.name == "cc-slack" and next(iter(call.args), None) in POST_VERBS for call in evt.cmd.calls())


@on(
    Event.PostToolUse,
    only_if=[Or(Tool("Bash"), Tool(*POST_TOOLS))],
    tests={
        Input(
            command="~/.claude/plugins/cache/example/cc-slack/1.0.0/bin/cc-slack reply --channel C0AAAAAAAA1 --thread 1790901035.467689 --no-watch --text hi",
            output=CC_SLACK_REPLY,
            session_id="900424b6-0000",
            commands={THREAD: "C0AAAAAAAA1/1790901035.467689 is on the drive's Slack watch list at state"},
        ): Warn(pattern=r"^C0AAAAAAAA1/1790901035\.467689 is on the drive's Slack watch list at state$"),
        Input(
            tool="mcp__plugin_cc-slack_cc-slack__slack_send",
            tool_input={"channel_id": "C0BBBBBBBB2", "text": "hi"},
            output=CC_SLACK_SEND,
            session_id="900424b6-0000",
            commands={THREAD: "C0BBBBBBBB2/1790875497.353829 is on the drive's Slack watch list at state"},
        ): Warn(pattern=r"^C0BBBBBBBB2/1790875497\.353829 is on the drive's Slack watch list at state$"),
        Input(
            tool="mcp__slack__slack_send_message",
            tool_input={"channel_id": "C0AAAAAAAA1", "thread_ts": "1790901035.467689", "text": "hi"},
            output=MCP_SENT,
            session_id="900424b6-0000",
            commands={THREAD: "C0AAAAAAAA1/1790901035.467689 is on the drive's Slack watch list at state"},
        ): Warn(pattern=r"^C0AAAAAAAA1/1790901035\.467689 is on the drive's Slack watch list at state$"),
        Input(
            tool="mcp__plugin_cc-slack_cc-slack__slack_reply",
            tool_input={"channel_id": "C0AAAAAAAA1", "text": "hi"},
            output=CC_SLACK_REPLY,
            session_id="5e55-0000",
            commands={THREAD: ""},
        ): Allow(),
        Input(command="cc-slack thread --url https://example.slack.com/archives/C0AAAAAAAA1/p1790901035467689", output=MCP_SENT): Allow(),
        Input(command="cc-slack whoami", output='{"channel_id":"D1","ts":"1.2"}'): Allow(),
        Input(tool="mcp__slack__slack_get_thread", tool_input={"channel_id": "C0AAAAAAAA1"}, output=MCP_SENT): Allow(),
        Input(command="cc-slack send --channel C1 --text hi", output="Error: permission denied"): Allow(),
    },
)
def register_posted_thread(evt: BaseHookEvent) -> HookResult | None:
    if not posts_to_slack(evt) or not (threads := posted_threads(evt.tool_response)):
        return None
    base = [sys.executable, str(script("drive.py")), "thread", "--session", evt.session_id, "--lane", named_lane(evt) or evt.session_id]
    base += ["--drive", drive] if (drive := reqenv.getenv("CLAUDE_LONG_RUNNING_DRIVE")) else []
    lines = []
    for thread in threads:
        argv = [*base, "--channel", thread.channel, "--thread-ts", thread.thread_ts, "--posted-ts", thread.posted_ts]
        try:
            done = subprocess.run(argv, capture_output=True, text=True, timeout=RECORD_TIMEOUT_SECONDS)
        except (subprocess.TimeoutExpired, OSError):
            done = None
        if done is None or done.returncode:
            lines.append(UNWATCHED.format(thread=f"{thread.channel}/{thread.thread_ts}"))
        elif done.stdout.strip():
            lines.append(done.stdout.strip())
    return evt.context("\n".join(lines)) if lines else None


@dataclass(frozen=True)
class SlackLink:
    channel: str
    message: str | None
    thread: str | None

    @property
    def flags(self) -> list[str]:
        named = (("--channel", self.channel), ("--message", self.message), ("--thread-ts", self.thread))
        return [arg for flag, value in named if value for arg in (flag, value)]


def slack_links(prompt: str) -> list[SlackLink]:
    found = (
        SlackLink(match["channel"], match["message"], match.groupdict().get("thread"))
        for pattern in LINKS
        for match in pattern.finditer(prompt)
    )
    return list(dict.fromkeys(found))


def fenced(lines: list[str]) -> str:
    return "\n".join([FENCE, *lines, FENCE])


def linked(prompt: str, thread: str | None = None) -> Input:
    commands = {WATCH: ON_WATCH.format(thread=thread) if thread else ""}
    return Input(prompt=prompt, state=ACTIVE, session_id="900424b6-0000", commands=commands)


@on(
    Event.UserPromptSubmit,
    only_if=[DriveActive(), OwnerPrompt(commands=True)],
    skip_if=[FromSubagent()],
    tests={
        linked(OWNER_LINK, "C0AAAAAAAA1/1790901035.467689"): Warn(
            pattern=r"^The Slack links in this prompt are on the drive's watch list[^\n]*\n~{12}\nC0AAAAAAAA1/1790901035\.467689 is on the drive's Slack watch list at state\n~{12}$"
        ),
        linked("look at C0BBBBBBBB2 p1790875497353829", "C0BBBBBBBB2/1790875497.353829"): Warn(pattern=r"C0BBBBBBBB2/1790875497\.353829 is on"),
        linked("/incident C0BBBBBBBB2 1790875497.353829 is broken", "C0BBBBBBBB2/1790875497.353829"): Warn(pattern=r"C0BBBBBBBB2/1790875497\.353829 is on"),
        linked("watch https://example.slack.com/archives/C0BBBBBBBB2 too", "C0BBBBBBBB2"): Warn(pattern=r"\nC0BBBBBBBB2 is on"),
        linked(OWNER_LINK): Allow(),
        Input(prompt="post in C0AAAAAAAA1 when the fix is live", state=ACTIVE): Allow(),
        Input(prompt="the canvas is https://example.slack.com/docs/T0AAAAAAAA1/F0AAAAAAAA1", state=ACTIVE): Allow(),
        Input(prompt="review https://github.com/example/repo/pull/1790901997422529", state=ACTIVE): Allow(),
        Input(prompt=OWNER_LINK): Allow(),
        Input(prompt=OWNER_LINK, agent_id="a1b2c3", state=ACTIVE): Allow(),
        Input(prompt=f'<teammate-message teammate_id="slack-watch">\n{OWNER_LINK}\n</teammate-message>', state=ACTIVE): Allow(),
    },
)
def watch_owner_links(evt: BaseHookEvent) -> HookResult | None:
    if not (links := slack_links(evt.user_prompt or "")):
        return None
    base = [sys.executable, str(script("drive.py")), "watch", "--session", evt.session_id, "--lane", OWNER]
    base += ["--drive", drive] if (drive := reqenv.getenv("CLAUDE_LONG_RUNNING_DRIVE")) else []
    added, missed = [], []
    for link in links:
        done, reason = run_drive([*base, *link.flags])
        if done is None or done.returncode:
            missed += [reason, shlex.join([*base, *link.flags])]
        elif done.stdout.strip():
            added.append(done.stdout.strip())
    parts = [*([WATCHING, fenced(added)] if added else []), *([NOT_WATCHING, fenced(missed)] if missed else [])]
    return evt.context(*parts) if parts else None
