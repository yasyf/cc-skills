from __future__ import annotations

import json
import re
import subprocess

from captain_hook import Allow, BaseHookEvent, Block, CommandSchema, Event, HookResult, Input, Operand, Option, Tool, Warn, on
from captain_hook.util import reqenv

from .compaction_handoff import Bound, CompactionState
from .gate5_release import OWNER_LANES

CC_SLACK = "mcp__plugin_cc-slack_cc-slack__"
CITE_UNREAD = "Warning, nothing was blocked: this Slack post cites a record that did not read, so it went out without that check."
WRITE_TOOLS = (f"{CC_SLACK}slack_send", f"{CC_SLACK}slack_reply", f"{CC_SLACK}slack_edit", f"{CC_SLACK}slack_dm_status", "mcp__slack__slack_send_message")
WRITE_VERBS = ("send", "reply", "edit", "dm-status")
CLI = CommandSchema(
    "cc-slack",
    operands=(Operand("words", count="*"),),
    options=(
        *(Option(name, (f"--{name}",)) for name in ("about", "agent", "blocks", "card", "channel", "cwd", "file", "session", "tap-pr", "text", "text-file", "thread", "ts", "url", "user")),
        *(Option(name, (f"--{name}",), bool) for name in ("broadcast", "mirrored", "root-only")),
    ),
)
STATUS_TOPIC = re.compile(r"\b(?:releas(?:e[sd]?|ing)|smoke|flak(?:e[sy]?|y|iness)|progress)\b", re.IGNORECASE)
CITED = re.compile(r"#(\d{5,})\b")
READ_SECONDS = 10
ACTIVE = [CompactionState(active=True, coordinator=True)]
LANE = {"CLAUDE_LONG_RUNNING_LANE": "release-5"}
STATUS_REFUSED = (
    "A drive lane writes nothing to Slack about a release, smoke run, flake or progress unless an owner record requests that post."
    " Report it to the coordinator on cci instead, or cite that owner record's seq as #<seq> in the post: a record of kind owner, or one from `owner` or `owner-relay`."
)


def drive_lane(evt: BaseHookEvent) -> bool:
    return bool(reqenv.getenv("CLAUDE_LONG_RUNNING_LANE")) or bool(evt.agent_id) and CompactionState.load(evt).active


def written(evt: BaseHookEvent) -> str:
    if evt.tool_name != "Bash":
        given = evt._tool_input
        card = given.get("card") or {}
        return "\n".join(str(part) for part in (given.get("text"), card.get("title"), card.get("body")) if part)
    texts = []
    for call in evt.command.calls("cc-slack"):
        values = CLI.bind(call).values
        if values.get("words", ())[:1] and values["words"][0] in WRITE_VERBS:
            texts += [str(text) for text in values.get("text", ())]
    return "\n".join(texts)


def owner_asked(text: str) -> bool:
    for seq in dict.fromkeys(CITED.findall(text)):
        done = subprocess.run(["cci", "grep", f"^#{seq} ", "--json", "-n", "1"], capture_output=True, text=True, timeout=READ_SECONDS)
        done.check_returncode()
        if any(owners(json.loads(line)) for line in done.stdout.splitlines() if line.strip()):
            return True
    return False


def owners(record: dict) -> bool:
    return record["kind"] == "owner" or record["lane"] in OWNER_LANES


@on(
    Event.PreToolUse,
    only_if=[Bound(), Tool("Bash", *WRITE_TOOLS)],
    tests={
        Input(tool=f"{CC_SLACK}slack_send", tool_input={"channel_id": "C0RELEASES1", "text": "Release 11927 converged on every cluster"}, env=LANE): Block(pattern=r"^A drive lane writes nothing to Slack about a release"),
        Input(tool=f"{CC_SLACK}slack_reply", tool_input={"channel_id": "C0RELEASES1", "thread_ts": "1.2", "text": "smoke passed on staging"}, agent_id="a1b2c3", state=ACTIVE): Block(pattern=r"cite that owner record"),
        Input(tool=f"{CC_SLACK}slack_dm_status", tool_input={"card": {"title": "Progress: 4 of 9 PRs landed", "body": "on track"}}, env=LANE): Block(),
        Input(command="cc-slack send --channel C0RELEASES1 --text 'flaky test quarantined'", env=LANE): Block(),
        Input(tool=f"{CC_SLACK}slack_send", tool_input={"channel_id": "C0RELEASES1", "text": "Release 11927 converged on every cluster"}, state=ACTIVE): Allow(),
        Input(tool=f"{CC_SLACK}slack_send", tool_input={"channel_id": "C0RELEASES1", "text": "Release 11927 converged on every cluster"}, agent_id="a1b2c3"): Allow(),
        Input(tool=f"{CC_SLACK}slack_reply", tool_input={"channel_id": "C0X", "thread_ts": "1.2", "text": "Thanks Dana, the viewer fix is in"}, env=LANE): Allow(),
        Input(
            tool=f"{CC_SLACK}slack_send",
            tool_input={"channel_id": "C0RELEASES1", "text": "Release 11927 is live, as you asked (#318299)"},
            env=LANE,
            commands={"cci grep": '{"seq": 318299, "lane": "board-watch", "kind": "owner"}\n'},
        ): Allow(),
        Input(
            tool=f"{CC_SLACK}slack_dm_status",
            tool_input={"card": {"title": "Release watch: pick again", "body": "Re-sent as buttons, as the owner asked (#360330)"}},
            env=LANE,
            commands={"cci grep": '{"seq": 360330, "lane": "owner-relay", "kind": "decide"}\n'},
        ): Allow(),
        Input(
            tool=f"{CC_SLACK}slack_send",
            tool_input={"channel_id": "C0RELEASES1", "text": "Release 11927 is live (#318301)"},
            env=LANE,
            commands={"cci grep": '{"seq": 318301, "lane": "desk-runner", "kind": "report"}\n'},
        ): Block(),
        Input(
            tool=f"{CC_SLACK}slack_send",
            tool_input={"channel_id": "C0RELEASES1", "text": "Release 11927 is live (#318300)"},
            env=LANE,
            commands={"cci grep": ""},
        ): Block(),
        Input(
            tool=f"{CC_SLACK}slack_send",
            tool_input={"channel_id": "C0RELEASES1", "text": "Release 11927 is live, as you asked (#318299)"},
            env=LANE,
            commands={"cci grep": (1, "cci: store down")},
        ): Warn(pattern=r"^Warning, nothing was blocked: this Slack post cites a record"),
        Input(command="cc-slack thread C0RELEASES1/p1 --text release", env=LANE): Allow(),
    },
)
def lanes_post_no_release_status(evt: BaseHookEvent) -> HookResult | None:
    if not drive_lane(evt) or not STATUS_TOPIC.search(text := written(evt)):
        return None
    try:
        return None if owner_asked(text) else evt.block(STATUS_REFUSED)
    except (OSError, subprocess.SubprocessError, ValueError, KeyError):
        return evt.context(CITE_UNREAD)
