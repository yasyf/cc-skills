from __future__ import annotations

import re
import subprocess

from captain_hook import Allow, And, BaseHookEvent, Block, Event, HookResult, Input, Or, Runs, Tool, on
from captain_hook.util import reqenv

from .compaction_handoff import LR, Bound, drive_entry
from .lane_slack_status import CC_SLACK, CLI
from .owner_facing import fenced

SEND_TOOLS = (f"{CC_SLACK}slack_send", f"{CC_SLACK}slack_reply", "mcp__slack__slack_send_message")
SEND_VERBS = ("send", "reply")
LINKED_CHANNEL = re.compile(r"/archives/([A-Z0-9]+)/")
REFUSED = "REFUSED "
CHECK_SECONDS = 20
UNMET = f"{LR} findings unmet"
RULE = "A send of a message he approved runs in the pass that read his tap; after it, the pick's lane first confirms the purpose is still unmet. Post the record below, or close the pick, then send again."
ENV = {"CLAUDE_LONG_RUNNING_DRIVE": "d", "CLAUDE_LONG_RUNNING_LANE": "owner-relay"}
STALE = "REFUSED #504257: his word in it is past the pass that read it. If what it asks for is still undone, lane-a posts `cci post --lane lane-a --kind answer --re 504257 --attr still-unmet=504257 \"<what is still undone>\"`."


def sent(tool: str = "slack_send", **given: object) -> Input:
    return Input(tool=f"{CC_SLACK}{tool}", tool_input={"channel_id": "U0TEAMMATE1", "text": "Could you add the repo secret?"} | given, env=ENV, commands={UNMET: (1, STALE)})


def recipients(evt: BaseHookEvent) -> list[str]:
    if evt.tool_name != "Bash":
        given = evt._tool_input
        linked = LINKED_CHANNEL.search(str(given.get("url") or ""))
        return [found] if (found := linked[1] if linked else str(given.get("channel_id") or "")) else []
    calls = [values for call in evt.command.calls("cc-slack") if (values := CLI.bind(call).values).get("words", ())[:1] and values["words"][0] in SEND_VERBS]
    linked = (LINKED_CHANNEL.search(str(url)) for values in calls for url in values.get("url", ()))
    return list(dict.fromkeys([*(str(channel) for values in calls for channel in values.get("channel", ())), *(found[1] for found in linked if found)]))


def refusal(drive: str, to: str) -> str | None:
    try:
        done = subprocess.run([str(LR), "findings", "unmet", "--drive", drive, "--send-to", to], capture_output=True, text=True, timeout=CHECK_SECONDS, env=dict(reqenv.env_map()))
    except (OSError, subprocess.TimeoutExpired):
        return None
    refused = done.stderr.strip()
    return refused if done.returncode == 1 and refused.startswith(REFUSED) else None


@on(  # capt-hook: no-escape a Slack write to another person under an owner pick past its pass needs his current word, which only the pick's lane confirms
    Event.PreToolUse,
    only_if=[Bound(), Or(Tool(*SEND_TOOLS), And(Tool("Bash"), Runs("cc-slack")))],
    tests={
        sent(): Block(pattern=r"^A send of a message he approved[\s\S]*REFUSED #504257"),
        sent("slack_reply", channel_id="", url="https://acme.slack.com/archives/C0GROUPDM1/p1794005461895789"): Block(pattern=r"REFUSED #504257"),
        Input(tool="mcp__slack__slack_send_message", tool_input={"channel_id": "U0TEAMMATE1", "text": "hi"}, env=ENV, commands={UNMET: (1, STALE)}): Block(),
        Input(tool=f"{CC_SLACK}slack_send", tool_input={"channel_id": "U0TEAMMATE1", "text": "hi"}, env=ENV, commands={UNMET: ""}): Allow(),
        Input(tool=f"{CC_SLACK}slack_send", tool_input={"channel_id": "U0TEAMMATE1", "text": "hi"}, env=ENV, commands={UNMET: (1, "lr: cci is unreachable")}): Allow(),
        Input(tool=f"{CC_SLACK}slack_send", tool_input={"channel_id": "U0TEAMMATE1", "text": "hi"}, commands={UNMET: (1, STALE)}): Allow(),
        Input(command="cc-slack send --channel U0TEAMMATE1 --text 'Could you add the repo secret?'", env=ENV, commands={UNMET: (1, STALE)}): Block(pattern=r"REFUSED #504257"),
        Input(command="cc-slack thread --url https://acme.slack.com/archives/C0GROUPDM1/p1794005461895789", env=ENV, commands={UNMET: (1, STALE)}): Allow(),
    },
)
def stale_pick_sends(evt: BaseHookEvent) -> HookResult | None:
    entry = drive_entry(evt)
    drive = entry["drive"] if entry else reqenv.getenv("CLAUDE_LONG_RUNNING_DRIVE")
    if not drive:
        return None
    refused = [found for to in recipients(evt) if (found := refusal(drive, to))]
    return evt.block(fenced(RULE, refused)) if refused else None
