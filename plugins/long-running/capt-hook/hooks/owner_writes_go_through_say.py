from __future__ import annotations

import json
import re
import subprocess

from captain_hook import Allow, BaseHookEvent, Block, Event, HookResult, Input, Tool, Warn, on

from .compaction_handoff import Bound
from .lane_slack_status import CC_SLACK, CLI
from .owner_answers import read
from .owner_replies_carry_news import HIS_DM, PEER
from .owner_returns import shipped_cc_slack
from .register_capture import OPERATOR
from .tests.register_fixtures import DRIVE_CONFIG

POSTS = (f"{CC_SLACK}slack_send", f"{CC_SLACK}slack_reply", "mcp__slack__slack_send_message")
STATUS = f"{CC_SLACK}slack_dm_status"
DM_URL = re.compile(r"/archives/(D[A-Z0-9]+)/")
HIS_VERBS = ("dm-status", "card-copy")
POST_VERBS = ("send", "reply")
BOARD_VERBS = ("ask", "send")
THROUGH_SAY = (
    "Every message to the owner goes out through `lr say` (the `say` tool), the one writer to his DM, his card threads and his board,"
    " so its rules refuse in one place and one message never posts twice. Load the `owner-message` skill and send this through `say`."
)
UNREAD_PEER = "Warning, nothing was blocked: this DM post was not checked against who it reaches, because `cc-slack dm-peer` did not read."
LANE = DRIVE_CONFIG | {"CLAUDE_LONG_RUNNING_LANE": "release-5"}
SOMEONE = json.dumps({"channel_id": "D0B6Z", "peer": "U0SOMEONE", "peer_name": "dana"})


def dm_channels(evt: BaseHookEvent) -> list[str]:
    if evt.tool_name != "Bash":
        given = evt._tool_input
        named = DM_URL.search(str(given.get("url") or ""))
        channel = named[1] if named else str(given.get("channel_id") or given.get("channel") or "")
        return [channel] if channel.startswith("D") else []
    channels = []
    for call in evt.command.calls("cc-slack"):
        values = CLI.bind(call).values
        if (values.get("words") or ("",))[0] not in POST_VERBS:
            continue
        channels += [str(channel) for channel in values.get("channel", ()) if str(channel).startswith("D")]
        channels += [named[1] for url in values.get("url", ()) if (named := DM_URL.search(str(url)))]
    return channels


def always_his(evt: BaseHookEvent) -> bool:
    if evt.tool_name == STATUS:
        return True
    if evt.tool_name != "Bash":
        return False
    slack = any((CLI.bind(call).values.get("words") or ("",))[0] in HIS_VERBS for call in evt.command.calls("cc-slack"))
    board = any(call.args[:1] and call.args[0] in BOARD_VERBS for call in evt.command.calls("cc-present"))
    return slack or board


@on(
    Event.PreToolUse,
    only_if=[Bound(), Tool("Bash", STATUS, *POSTS)],
    tests={
        Input(tool=f"{CC_SLACK}slack_send", tool_input={"channel_id": "D0B6Z", "text": "The relay fix is live."}, env=LANE, commands={PEER: HIS_DM}): Block(pattern=r"^Every message to the owner goes out through `lr say`"),
        Input(tool=f"{CC_SLACK}slack_reply", tool_input={"url": "https://brook.slack.com/archives/D0B6Z/p1793635190051179", "text": "Live now."}, env=LANE, commands={PEER: HIS_DM}): Block(),
        Input(tool=STATUS, tool_input={"text": "Approve #50026?"}, env=LANE): Block(),
        Input(command="cc-slack dm-status 'Approve #50026?'", env=LANE): Block(),
        Input(command="cc-slack card-copy --url https://brook.slack.com/archives/D0B6Z/p1793635190051179 --thread 1.2", env=LANE): Block(),
        Input(command="cc-slack reply --channel D0B6Z --thread 1.2 --text 'Live now.'", env=LANE, commands={PEER: HIS_DM}): Block(),
        Input(command="cc-present ask card.json", env=LANE): Block(),
        Input(tool=f"{CC_SLACK}slack_send", tool_input={"channel_id": "D0B6Z", "text": "Thanks for the report."}, env=LANE, commands={PEER: SOMEONE}): Allow(),
        Input(tool=f"{CC_SLACK}slack_send", tool_input={"channel_id": "D0B6Z", "text": "Live now."}, env=LANE, commands={PEER: (1, "cc-slack can't reach the daemon")}): Warn(pattern=r"^Warning, nothing was blocked: this DM post"),
        Input(tool=f"{CC_SLACK}slack_reply", tool_input={"channel_id": "C0RELEASES1", "thread_ts": "1.2", "text": "Live now."}, env=LANE): Allow(),
        Input(command="cc-slack delete --url https://brook.slack.com/archives/D0B6Z/p1793635190051179", env=LANE): Allow(),
        Input(command="cc-slack pickup --channel D0B6Z --ts 1.2", env=LANE): Allow(),
        Input(command="cc-present outcomes --new", env=LANE): Allow(),
        Input(tool=f"{CC_SLACK}slack_send", tool_input={"channel_id": "D0B6Z", "text": "Live now."}): Allow(),
    },
)
def owner_writes_go_through_say(evt: BaseHookEvent) -> HookResult | None:
    if always_his(evt):
        return evt.block(THROUGH_SAY)
    if not (channels := dm_channels(evt)):
        return None
    try:
        peers = [read([str(shipped_cc_slack()), "dm-peer", "--channel", channel]) for channel in channels]
        reaches_him = any(json.loads(peer)["peer"] == OPERATOR for peer in peers if peer is not None)
    except (OSError, subprocess.SubprocessError, ValueError, KeyError):
        return evt.context(UNREAD_PEER)
    if reaches_him:
        return evt.block(THROUGH_SAY)
    return evt.context(UNREAD_PEER) if None in peers else None
