from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from captain_hook import Allow, BaseHookEvent, Block, CommandSchema, Event, HookResult, Input, Operand, Option, Tool, TranscriptFixture, Warn, on
from captain_hook.annotations import dispatch_prompt, session_annotations
from captain_hook.util import reqenv

from .compaction_handoff import Bound, CompactionState
from .gate5_release import OWNER_LANES
from .refusal_kinds_need_owner_answer import BRIEF_NAMED

CC_SLACK = "mcp__plugin_cc-slack_cc-slack__"
CITE_UNREAD = "Warning, nothing was blocked: the owner record or grant this lane's brief cites did not read, so the post went out without that check."
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
NOTE_ID = re.compile(r"\b[0-9a-f]{7,40}\b")
ARCHIVED = re.compile(r"/archives/([CDG][A-Z0-9]+)")
GRANT = "grant"
GRANT_LABEL = "slack-grant:"
READ_SECONDS = 10
ACTIVE = [CompactionState(active=True, coordinator=True)]
LANE = {"CLAUDE_LONG_RUNNING_LANE": "release-5"}
STATUS_REFUSED = (
    "A drive lane writes nothing to Slack about a release, smoke run, flake or progress unless an owner record or Slack grant requests that post."
    " Report it to the coordinator on cci instead, or cite the record's `#<seq>` or the grant's answer id in the lane's brief or a `ccx: grant=<id>` line."
)
BRIEFS = Path(__file__).parent / "tests" / "fixtures" / "briefs"
GRANT_ID = "0a1b2c3d4e5f60718293a4b5c6d7e8f901234567"
GRANTED = json.dumps([{"id": GRANT_ID, "tags": ["slack", f"{GRANT_LABEL}C0ALERTS01"], "superseded_by": None}])
MECHANISM = "These requests failed because the API routed them to the wrong cluster. A fix is in progress."
POSTER_BRIEF = (
    "ccx: incident=none\n"
    "You are a one-shot posting lane: post one mechanism line as a reply in the alert's thread, then return.\n"
    "Authority: the owner's standing grant, cc-notes answer 0a1b2c3, allows one-line mechanism replies in an alert's own thread."
)


@dataclass(frozen=True, slots=True)
class Post:
    text: str
    channels: frozenset[str]


def briefed(*lines: str) -> TranscriptFixture:
    return TranscriptFixture([{"type": "user", "message": {"role": "user", "content": "\n".join(lines)}}])


def drive_lane(evt: BaseHookEvent) -> bool:
    return bool(reqenv.getenv("CLAUDE_LONG_RUNNING_LANE")) or bool(evt.agent_id) and CompactionState.load(evt).active


def written(evt: BaseHookEvent) -> Post:
    if evt.tool_name != "Bash":
        given = evt._tool_input
        card = given.get("card") or {}
        return Post("\n".join(str(part) for part in (given.get("text"), card.get("title"), card.get("body")) if part), frozenset(filter(None, [given.get("channel_id")])))
    calls = [values for call in evt.command.calls("cc-slack") if (values := CLI.bind(call).values).get("words", ())[:1] and values["words"][0] in WRITE_VERBS]
    urls = (ARCHIVED.search(str(url)) for values in calls for url in values.get("url", ()))
    return Post(
        "\n".join(str(text) for values in calls for text in values.get("text", ())),
        frozenset([*(str(channel) for values in calls for channel in values.get("channel", ())), *(found[1] for found in urls if found)]),
    )


def lane_authority(evt: BaseHookEvent) -> str:
    prompt = dispatch_prompt(evt)
    briefs = (path.read_text(errors="replace") for found in BRIEF_NAMED.findall(prompt) if (path := Path(found).expanduser()).is_file())
    return "\n".join([prompt, *briefs])


def owner_asked(evt: BaseHookEvent, post: Post) -> bool:
    said = lane_authority(evt)
    noted = {value for found in (evt.annotations, session_annotations(evt)) if (value := found.get(GRANT))}
    seqs = [*dict.fromkeys([*CITED.findall(said), *filter(str.isdigit, noted)])]
    return owner_recorded(evt, seqs) or granted(evt, post.channels, {*NOTE_ID.findall(said), *noted})


def read(evt: BaseHookEvent, argv: list[str]) -> str:
    done = subprocess.run(argv, capture_output=True, text=True, timeout=READ_SECONDS, cwd=str(evt.cwd or reqenv.cwd()), env=reqenv.env_map())
    done.check_returncode()
    return done.stdout


def owner_recorded(evt: BaseHookEvent, seqs: list[str]) -> bool:
    if not seqs:
        return False
    found = read(evt, ["cci", "grep", f"^#({'|'.join(seqs)}) ", "--json", "-n", str(len(seqs))])
    return any(owners(json.loads(line)) for line in found.splitlines() if line.strip())


def owners(record: dict) -> bool:
    return record["kind"] == "owner" or record["lane"] in OWNER_LANES


def granted(evt: BaseHookEvent, channels: frozenset[str], cited: set[str]) -> bool:
    for channel in sorted(channels) if cited else ():
        label = f"{GRANT_LABEL}{channel}"
        for answer in json.loads(read(evt, ["ccn", "answer", "search", label, "--json", "--limit", "0", "-R", str(evt.cwd or reqenv.cwd())])):
            if not answer.get("superseded_by") and any(tag.partition("/")[0] == label for tag in answer.get("tags", ())) and any(answer["id"].startswith(token) for token in cited):
                return True
    return False


@on(
    Event.PreToolUse,
    only_if=[Bound(), Tool("Bash", *WRITE_TOOLS)],
    tests={
        Input(tool=f"{CC_SLACK}slack_send", tool_input={"channel_id": "C0RELEASES1", "text": "Release 11927 converged on every cluster"}, env=LANE): Block(pattern=r"^A drive lane writes nothing to Slack about a release"),
        Input(tool=f"{CC_SLACK}slack_reply", tool_input={"channel_id": "C0RELEASES1", "thread_ts": "1.2", "text": "smoke passed on staging"}, agent_id="a1b2c3", state=ACTIVE): Block(pattern=r"cite the record's `#<seq>`"),
        Input(tool=f"{CC_SLACK}slack_dm_status", tool_input={"card": {"title": "Progress: 4 of 9 PRs landed", "body": "on track"}}, env=LANE): Block(),
        Input(command="cc-slack send --channel C0RELEASES1 --text 'flaky test quarantined'", env=LANE): Block(),
        Input(tool=f"{CC_SLACK}slack_send", tool_input={"channel_id": "C0RELEASES1", "text": "Release 11927 converged on every cluster"}, state=ACTIVE): Allow(),
        Input(tool=f"{CC_SLACK}slack_send", tool_input={"channel_id": "C0RELEASES1", "text": "Release 11927 converged on every cluster"}, agent_id="a1b2c3"): Allow(),
        Input(tool=f"{CC_SLACK}slack_reply", tool_input={"channel_id": "C0X", "thread_ts": "1.2", "text": "Thanks Dana, the viewer fix is in"}, env=LANE): Allow(),
        Input(
            tool=f"{CC_SLACK}slack_reply",
            tool_input={"channel_id": "C0ALERTS01", "thread_ts": "1.2", "agent": "claude", "no_watch": True, "text": MECHANISM},
            agent_id="a1b2c3",
            state=ACTIVE,
            transcript=briefed(POSTER_BRIEF),
            commands={"ccn answer search": GRANTED},
        ): Allow(),
        Input(
            tool=f"{CC_SLACK}slack_reply",
            tool_input={"channel_id": "C0ALERTS01", "thread_ts": "1.2", "agent": "claude", "no_watch": True, "text": f"{MECHANISM} #318299"},
            agent_id="a1b2c3",
            state=ACTIVE,
            commands={"cci grep": '{"seq": 318299, "lane": "owner-relay", "kind": "owner"}\n'},
        ): Block(pattern=r"in the lane's brief"),
        Input(
            tool=f"{CC_SLACK}slack_reply",
            tool_input={"channel_id": "C0RELEASES1", "thread_ts": "1.2", "text": MECHANISM},
            agent_id="a1b2c3",
            state=ACTIVE,
            transcript=briefed(POSTER_BRIEF),
            commands={"ccn answer search": "[]"},
        ): Block(),
        Input(
            tool=f"{CC_SLACK}slack_reply",
            tool_input={"channel_id": "C0ALERTS01", "thread_ts": "1.2", "text": MECHANISM},
            env=LANE,
            transcript=briefed(f"Lane alert-comms: read {BRIEFS / 'alert-comms.md'} in full first and execute it exactly."),
            commands={"ccn answer search": GRANTED},
        ): Allow(),
        Input(command="cc-slack reply --url https://example.slack.com/archives/C0ALERTS01/p1 --text 'A fix is in progress'  # ccx:grant=0a1b2c3", env=LANE, commands={"ccn answer search": GRANTED}): Allow(),
        Input(
            tool=f"{CC_SLACK}slack_send",
            tool_input={"channel_id": "C0RELEASES1", "text": "Release 11927 is live"},
            env=LANE,
            transcript=briefed("Post the release line in the releases channel, as the owner asked (#318299)."),
            commands={"cci grep": '{"seq": 318299, "lane": "board-watch", "kind": "owner"}\n'},
        ): Allow(),
        Input(
            tool=f"{CC_SLACK}slack_dm_status",
            tool_input={"card": {"title": "Release watch: pick again", "body": "Re-sent as buttons"}},
            env=LANE,
            transcript=briefed("Re-send the release watch pick as buttons, as the owner asked (#360330)."),
            commands={"cci grep": '{"seq": 360330, "lane": "owner-relay", "kind": "decide"}\n'},
        ): Allow(),
        Input(
            tool=f"{CC_SLACK}slack_send",
            tool_input={"channel_id": "C0RELEASES1", "text": "Release 11927 is live"},
            env=LANE,
            transcript=briefed("Post the release line (#318301)."),
            commands={"cci grep": '{"seq": 318301, "lane": "desk-runner", "kind": "report"}\n'},
        ): Block(),
        Input(
            tool=f"{CC_SLACK}slack_send",
            tool_input={"channel_id": "C0RELEASES1", "text": "Release 11927 is live"},
            env=LANE,
            transcript=briefed("Post the release line (#318300)."),
            commands={"cci grep": ""},
        ): Block(),
        Input(
            tool=f"{CC_SLACK}slack_send",
            tool_input={"channel_id": "C0RELEASES1", "text": "Release 11927 is live"},
            env=LANE,
            transcript=briefed("Post the release line, as the owner asked (#318299)."),
            commands={"cci grep": (1, "cci: store down")},
        ): Warn(pattern=r"^Warning, nothing was blocked: the owner record or grant this lane's brief cites"),
        Input(command="cc-slack thread C0RELEASES1/p1 --text release", env=LANE): Allow(),
    },
)
def lanes_post_no_release_status(evt: BaseHookEvent) -> HookResult | None:
    if not drive_lane(evt) or not STATUS_TOPIC.search((post := written(evt)).text):
        return None
    try:
        return None if owner_asked(evt, post) else evt.block(STATUS_REFUSED)
    except (OSError, subprocess.SubprocessError, ValueError, KeyError):
        return evt.context(CITE_UNREAD)
