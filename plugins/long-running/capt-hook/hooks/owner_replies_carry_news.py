from __future__ import annotations

import json
import re
import subprocess

from captain_hook import Allow, BaseHookEvent, Block, Event, HookResult, Input, Tool, Warn, on

from .compaction_handoff import Bound, CompactionState
from .lane_slack_status import CC_SLACK, CLI, drive_lane
from .owner_answers import read
from .owner_returns import SHIPPED_CC_SLACK, shipped_cc_slack
from .register_capture import OPERATOR
from .tests.register_fixtures import DRIVE_CONFIG

MIN_WORDS = 12
NEWS = re.compile(r"\d|https?://|\?")
DM_URL = re.compile(r"/archives/(D[A-Z0-9]+)/")
NO_NEWS = (
    "A short reply to the owner with no number, link or question only costs him a notification."
    " Run `slack_react` with `name` `+1` on his message instead, or reply with the condition, correction, time or answer he lacks."
)
UNREAD_PEER = "Warning, nothing was blocked: this short DM reply was not checked against who it reaches, because `cc-slack dm-peer` did not read."
HIS_DM = json.dumps({"channel_id": "D0B6Z", "peer": OPERATOR, "peer_name": "owner"})
PEER = f"{SHIPPED_CC_SLACK} dm-peer"
LANE = DRIVE_CONFIG | {"CLAUDE_LONG_RUNNING_LANE": "release-5"}
ACTIVE = [CompactionState(active=True, communicator=True)]


def reply(text: str, peer: str | tuple = HIS_DM, **given: object) -> Input:
    return Input(tool=f"{CC_SLACK}slack_reply", tool_input={"channel_id": "D0B6Z", "thread_ts": "1793635190.051179", "text": text, **given}, env=LANE, commands={PEER: peer})


@on(
    Event.PreToolUse,
    only_if=[Bound(), Tool("Bash", f"{CC_SLACK}slack_reply", f"{CC_SLACK}slack_send")],
    tests={
        reply("Got it, on it."): Block(pattern=r"^A short reply to the owner.*`slack_react`"),
        Input(tool=f"{CC_SLACK}slack_reply", tool_input={"url": "https://brook.slack.com/archives/D0B6Z/p1793635190051179", "text": "Got it."}, env=LANE, commands={PEER: HIS_DM}): Block(),
        Input(tool=f"{CC_SLACK}slack_send", tool_input={"channel_id": "D0B6Z", "text": "Thanks, understood."}, state=ACTIVE, env=DRIVE_CONFIG, commands={PEER: HIS_DM}): Block(),
        Input(command="cc-slack reply --channel D0B6Z --text 'Got it.'", env=LANE, commands={PEER: HIS_DM}): Block(),
        reply("Merged at 3:10."): Allow(),
        reply("See https://brook.ts.net/dash."): Allow(),
        reply("Which one did you mean?"): Allow(),
        reply("That changes the plan, since the old path stays and the new one waits behind it."): Allow(),
        reply("Got it.", peer=json.dumps({"channel_id": "D0B6Z", "peer": "U0SOMEONE", "peer_name": "dana"})): Allow(),
        reply("Got it.", file="/tmp/shot.png"): Allow(),
        reply("Got it.", peer=(1, "cc-slack can't reach the daemon")): Warn(pattern=r"^Warning, nothing was blocked: this short DM reply"),
        Input(tool=f"{CC_SLACK}slack_reply", tool_input={"channel_id": "C0RELEASES1", "thread_ts": "1.2", "text": "Got it."}, env=LANE): Allow(),
        Input(tool=f"{CC_SLACK}slack_reply", tool_input={"channel_id": "D0B6Z", "thread_ts": "1.2", "text": "Got it."}): Allow(),
        Input(command="ls", env=LANE): Allow(),
    },
)
def owner_replies_carry_news(evt: BaseHookEvent) -> HookResult | None:
    if not (drive_lane(evt) or CompactionState.load(evt).active):
        return None
    channels = [channel for channel, text in dm_posts(evt) if text.strip() and len(text.split()) < MIN_WORDS and not NEWS.search(text)]
    if not channels:
        return None
    try:
        peers = [read([str(shipped_cc_slack()), "dm-peer", "--channel", channel]) for channel in channels]
        reaches_him = any(json.loads(peer)["peer"] == OPERATOR for peer in peers if peer is not None)
    except (OSError, subprocess.SubprocessError, ValueError, KeyError):
        return evt.context(UNREAD_PEER)
    if reaches_him:
        return evt.block(NO_NEWS)
    return evt.context(UNREAD_PEER) if None in peers else None


def dm_posts(evt: BaseHookEvent) -> list[tuple[str, str]]:
    if evt.tool_name != "Bash":
        given = evt._tool_input
        if given.get("blocks") or given.get("file"):
            return []
        named = DM_URL.search(str(given.get("url") or ""))
        channel = named[1] if named else str(given.get("channel_id") or "")
        return [(channel, str(given.get("text") or ""))] if channel.startswith("D") else []
    posts = []
    for call in evt.command.calls("cc-slack"):
        values = CLI.bind(call).values
        verb, *words = values.get("words") or ("",)
        if verb in ("reply", "send") and not values.get("blocks") and not values.get("file"):
            text = "\n".join([*map(str, values.get("text", ())), *map(str, words)])
            posts += [(str(channel), text) for channel in values.get("channel", ()) if str(channel).startswith("D")]
    return posts
