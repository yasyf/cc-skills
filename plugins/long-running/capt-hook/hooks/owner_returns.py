from __future__ import annotations

import html
import json
import re
import subprocess
import time
from itertools import pairwise
from collections.abc import Sequence
from pathlib import Path

from spawnllm import Binary, BinaryAnswer, Refused

from captain_hook import Allow, AssistantMessage, BaseHookEvent, Block, Event, FromSubagent, HookResult, Input, Or, Rewrite, T, Tool, Warn, WorkflowState, hook, on, workflow_state
from captain_hook.util import reqenv

from .compaction_handoff import bus_name, Communicator, COMMUNICATOR_LANE, communicator_tests, CompactionState, drive_entry, ROOT_LANE
from .owner_answers import read, RECORD_BYTES
from .lane_slack_status import CC_SLACK, CLI, WRITE_TOOLS
from .register_capture import OPERATOR, OWNER_WATCH, owner_messages_heard, queued_prompts, slack_dms
from .register_gates import blocker, cause
from .root_context import CC_SLACK as CC_SLACK_CLI, OwnerPrompt, ReadsNewestSlackMessage, runs_verb, SLACK_THREAD_TOOLS
from .root_replies import owner_message, REPLY_WINDOW
from .tests.register_fixtures import DRIVE_CONFIG

RETURN_GAP_SECONDS = 60 * 60
LINK = re.compile(r"https?://\S+")
RETURNED = "The owner is back after {minutes} minutes away. Open this reply with the dashboard link on its first line, then what changed in a few lines; the full table lives on the dashboard."
MISSING = "A reply to the owner's return opens with the dashboard link on its first line, and this one does not. Rewrite it with the link first, then what changed in a few lines."
LETTERED = re.compile(r"^[ \t>*-]*\(?([A-H])[).:]\**\s|(?<![\w(])\(([A-H])\)\s|(?<![\w(])([A-H])\)\**\s", re.MULTILINE)
CHOICE = re.compile(
    r"\b(?i:option) \(?[A-H1-9]\b|(?i:\bpick one\b|\bwhich (?:do you want|would you like|do you prefer|one do you)\b)"
    r"|(?i:\b(?:reply|respond|answer)) with (?:(?i:the |a )?(?i:letter|number)|[A-H1-9]\b)"
)
DM_CARD = "slack_dm_status"
REPLY = "mcp__plugin_long-running_lr__reply"
OWNER_MARK = "mcp__plugin_long-running_lr__owner_mark"
LEDGER_SURFACE = re.compile(r"^- ledger: m\d", re.MULTILINE)
REPLIED = "Its checks passed.\n\n- Land it (recommended)\n- Hold it\n\nReply r1; pass this id to edit it on every surface.\n- board: card-r1\n- slack: https://brook.slack.com/archives/D0B6Z/p1793635190051179\n- record: #12@2479ec97"
SLACK_POSTED = re.compile(r"\bts\W{1,4}\d{10}\.\d{6}|/archives/\w+/p\d{16}")
BOARD_VERBS = frozenset({"push", "update-block", "ask", "send"})
INLINE_PICK = "A pick for the owner never sits inline or in plain Slack text; he answers with a button. Send it with the `reply` tool, the answers as its `picks` and one of them as `recommended`, then stop again."
PLAIN_PICK = "Warning, nothing was blocked: A pick for the owner is never plain text in his Slack DM; he taps a button. Send it with the `reply` tool, the answers as its `picks` and one of them as `recommended`."
PICK = "Two ways to fix the relay:\n\nA) Keep one shared replica\nB) Give each chat its own\n\nWhich do you want?"
REMINDER = Binary(
    "The owner's words this turn are below; his typos are his, so read through them."
    " Yes when he reminds the assistant of something it should already have done, repeats an instruction or ruling he gave before, or corrects how it works: something missed, forgotten, still happening, done wrong or not followed."
    " No for a new request, a question, an answer to a question, a pick, an approval or thanks that points at nothing the assistant failed to do."
)
ASKED_WHY = re.compile(r"\bwhy\b|\bhow come\b|\bhow (?:does|do|did|is|are|was|were)\b|\bwhat happened\b", re.IGNORECASE)
UNROUTED_REMINDER = (
    "Every owner reminder or correction spawns a meta tooling lane that makes the behaviour automatic."
    " Before stopping, post one ask to meta naming his ledger row, `owner:m<row>` plus your one-line order, or launch the lane with `lr launch`."
)
WAIT_TOOLS = frozenset({"Agent", "Task", "Monitor"})
SUBAGENT_TOOLS = frozenset({"Agent", "Task"})
FILE_PATH = (
    "The owner never opens a file, so a reply to him names no path under the drive's state dir, a scratch dir or progress-desk."
    " Link the dashboard section that shows it on the tailnet instead, and say what it shows in plain words."
)
SCRATCH_ROOTS = (".claude/scratch/", "progress-desk/")
CC_SLACK_CACHE = Path("plugins") / "cache" / "cc-slack" / "cc-slack"
VERSION = re.compile(r"\d+(?:\.\d+)*")
SHIPPED_CC_SLACK = Path(DRIVE_CONFIG["CLAUDE_CONFIG_DIR"]) / CC_SLACK_CACHE / "0.4.27" / "bin" / "cc-slack"
CC_SLACK_UNREAD = "The communicator could not run `cc-slack {verb}` from the plugin ({why}), so the owner's DM replies go unwatched or unchecked until it works."
UNANSWERED = (
    "An owner Slack message has no answer, and a mark or a lane does not answer a message in a thread."
    " Spawn the subagent that answers each one, or answer it now with the `reply` tool, its `order` naming his row so it posts in his thread, or `slack_react` `+1` when it needs nothing back.\n```\n{replies}\n```"
)
ACK_AFTER_SECONDS = 30
UNACKED = "An owner Slack message has waited over 30 seconds with no thread reply. Answer before any routing with the `reply` tool, or `slack_react` `+1` with `url` {permalink} when it needs nothing back."
WATCH_COMMAND = "lr owner watch"
WATCH_CALL = f"`Monitor` with description `{OWNER_WATCH}`, command `{WATCH_COMMAND}` and `timeout_ms` 1800000"
ARM_WATCH = f"An idle window hears the owner's Slack messages only through its standing watch. Arm {WATCH_CALL} unless it already runs, and again each time it ends."
UNWATCHED = f"The owner's Slack watch is not running, so his next message waits until this window moves. Arm {WATCH_CALL}, then stop."
WATCHING = [{"id": "w1", "type": "shell", "status": "running", "description": OWNER_WATCH, "command": WATCH_COMMAND}]
HOLDER = "900424b6-0000"
OWED_REPLY = {"thread_ts": "1793684147.131059", "ts": "1793705742.180139", "text": "A, but as buttons", "permalink": "https://acme.slack.com/archives/D0B6Z/p1793705742180139"}
IN_THREAD = "He asked in a Slack thread, so this reply posts in that thread."
THREAD_READ = "The communicator reads only the Slack thread it is answering, one the owner wrote in. Ask a lane with `lr ask-lane '<question>'` for any other thread, and its answer arrives as a record."
EXPLAINER = re.compile(r"https?://[^\s/]+/(?:one-pagers|design-docs)/[\w.-]+/")
TITLE = re.compile(r"<title>(.*?)</title>", re.DOTALL)
EXPLAIN_TOPIC = "lr-explain"
SYSTEM_RAISED = Binary(
    "The owner's words this turn are below, then the titles of the one-pagers already hosted for him; his typos are his, so read through them."
    " Yes only when he asks a design question, how a system works or is meant to work, and no listed page already explains it."
    " No for a bug report, a complaint, a screenshot of something broken, an order to fix or build something, status, a pick, an approval, thanks, or a topic a listed page covers."
)
RAISED = "He asked how a system works or is meant to work. Before replying, run `lr explain <topic> --from <markdown with a fenced arch block holding its architecture diagram>` and put the link it prints in the reply."
UNEXPLAINED = (
    "He asked how a system works, so the reply carries a hosted one-pager."
    " Run `lr explain <topic> --from <markdown file or cci record whose diagram is a fenced arch block>` and put its link in this reply; if the verb fails it files a defect, and the reply says the page is not up."
)
NO = BinaryAnswer(p_yes=0.02, confidence=0.95)
YES = BinaryAnswer(p_yes=0.97, confidence=0.94)
SIGNAL = "how does the signal server decide which replica a chat talks to"
WHY = "The relay dropped your message because both chats shared one replica, so the second chat's write replaced the first."
PAGE = "https://mac.tail1.ts.net:8665/one-pagers/relay-replicas/"
CALM = {"reminder": NO}
REMINDED = CALM | {"reminder": BinaryAnswer(p_yes=0.97, confidence=0.94)}
HIS_1_34_AM = (
    "every tjme i need to remknd you to do skemtjjng, hou need to spawn a meta tooling lane foe thay (includkng tbis)."
    " uou did tjay for the ildate after being away a long tome, so good job, but make sure thay is durable behavior not just this session."
)
ASKS = "cci tail --drive brook-drive --lane owner-relay --lane root"
EXPLAIN_DEFECTS = "cci tail --drive brook-drive --lane owner-relay --kind defect --topic lr-explain"
FILED_DEFECT = json.dumps({"seq": 366200, "lane": "owner-relay", "kind": "defect", "topic": "lr-explain", "text": "lr explain could not host its page on relay"})
TRIED_EXPLAIN = T.tool_turn("Bash", result="fatal: repository not found", command="lr explain relay --from /notes/relay.md")
SENT = '{"ok": true, "channel": "D0B6Z", "ts": "1793635190.051179"}'
CARDED = '{"channel_id": "D0B6Z", "thread_ts": "1793635190.051179", "ts": "1793635190.051179"}'
RELAY_CARD = {"title": "How do we fix the relay?", "body": "Each chat keeps its own replica, or they share one.", "options": ["Keep one shared replica", "Give each chat its own"], "recommended": "Give each chat its own"}
RELAY_PR = "https://github.com/yasyf/cc-skills/pull/2196"
LAND_CARD = {"title": "Land the relay fix tonight?", "body": f"Its checks passed: {RELAY_PR}", "options": ["Land", "Hold"], "recommended": "Land"}
RESUMED = T.user("This session is being continued from a previous conversation that ran out of context.")
CCI_WAKE = T.user('<channel source="plugin:cc-inbox:cci" kind="correction" lane="relay-join-1" seq="366942">Correction to 366939.</channel>', isMeta=True)
TEAMMATE_WAKE = T.user('Another Claude session sent a message:\n<teammate-message teammate_id="spot-pool" color="green" summary="Correction">You missed the m11 tip again.</teammate-message>')
NO_OUTPUT = T.user("[Your previous response had no visible output. Please continue and produce a user-visible response.]", isMeta=True)
REMINDER_FEEDBACK = T.user(f"Stop hook feedback:\n{UNROUTED_REMINDER}", isMeta=True)


@workflow_state("long_running_row_threads")
class RowThreads(WorkflowState):
    threads: dict[str, str] = {}


@workflow_state("long_running_owner_returns")
class OwnerReturns(WorkflowState):
    prompted_at: float | None = None
    returned: bool = False
    explain_due: bool = False
    asked_why: bool = False
    marked_replies: list[str] = []


def away(minutes: float) -> list:
    return [CompactionState(active=True, communicator=True), OwnerReturns(prompted_at=time.time() - 60 * minutes)]


def back(reply: str, returned: bool = True) -> Input:
    return Input(transcript=[T.user("where are we"), T.assistant(reply)], state=[CompactionState(active=True, communicator=True), OwnerReturns(returned=returned)], decide=CALM)


@on(
    Event.SessionStart | Event.UserPromptSubmit,
    only_if=[Communicator()],
    skip_if=[FromSubagent()],
)
def communicator_holds_the_owners_dm(evt: BaseHookEvent) -> None:
    if me := cc_slack(evt, "whoami"):
        cc_slack(evt, "follow", "--channel", json.loads(me)["dm_channel_id"], "--session", evt.session_id)


@on(
    Event.UserPromptSubmit,
    only_if=[Communicator()],
    skip_if=[FromSubagent()],
    tests=communicator_tests({
        Input(prompt="where are we", state=away(61), env=DRIVE_CONFIG): Warn(pattern=r"^The owner is back after 61 minutes away\..*dashboard link on its first line"),
        Input(prompt="where are we", state=away(59), env=DRIVE_CONFIG): Allow(),
        Input(prompt="where are we", state=[CompactionState(active=True, communicator=True)], env=DRIVE_CONFIG): Allow(),
        Input(prompt="<task-notification>update-desk finished</task-notification>", state=away(61), env=DRIVE_CONFIG): Allow(),
        Input(prompt="where are we", state=away(61), env=DRIVE_CONFIG, agent_id="a1b2c3"): Allow(),
    }),
)
def owner_return_opens_with_the_update(evt: BaseHookEvent) -> HookResult | None:
    now, owner = time.time(), OwnerPrompt().check(evt)
    with OwnerReturns.mutate(evt) as state:
        gap = now - (state.prompted_at or now)
        state.returned = owner and gap >= RETURN_GAP_SECONDS
        state.prompted_at = now if owner else state.prompted_at
    if not state.returned:
        return None
    return evt.context(RETURNED.format(minutes=int(gap // 60)))


def delivered(thread: str = OWED_REPLY["thread_ts"]) -> list:
    payload = {"type": "slack.message", "channel_id": "D0B6Z", "thread_ts": thread, "ts": OWED_REPLY["ts"], "user": OPERATOR, "from_claude": False, "text": OWED_REPLY["text"]}
    tagged = f'<channel source="plugin:cc-slack:cc-slack" subject_id="50c3b930" type="slack.message">\n{json.dumps(payload)}\n</channel>'
    return [T.user("where are we", timestamp="2026-11-03T16:59:00Z"), {"type": "attachment", "timestamp": "2026-11-03T17:00:00Z", "attachment": {"type": "queued_command", "prompt": tagged, "commandMode": "prompt"}}]


@on(
    Event.SessionStart,
    only_if=[Communicator()],
    skip_if=[FromSubagent()],
    tests=communicator_tests({
        Input(source="compact", state=[CompactionState(active=True, communicator=True)]): Warn(pattern=r"^An idle window hears.*`lr owner watch`"),
        Input(source="startup", state=[CompactionState(active=True, communicator=True)]): Warn(pattern=r"`lr owner watch`"),
        Input(source="resume", state=[CompactionState(active=True, communicator=True)]): Warn(pattern=r"`lr owner watch`"),
        Input(source="compact", agent_id="a1b2c3", state=[CompactionState(active=True, communicator=True)]): Allow(),
    }),
)
def communicator_arms_the_owner_watch(evt: BaseHookEvent) -> HookResult | None:
    return evt.context(ARM_WATCH)


def watch_queued(thread: str) -> list:
    payload = {"type": "slack.message", "watch_id": 742, "kind": "dm", "channel_id": "D0B6Z", "thread_ts": thread, "ts": OWED_REPLY["ts"], "user": OPERATOR, "from_claude": False, "text": OWED_REPLY["text"]}
    notified = f'<task-notification>\n<task-id>bi4wzi4te</task-id>\n<summary>Monitor event: "{OWNER_WATCH}"</summary>\n<event>{json.dumps(payload)}</event>\n</task-notification>'
    queued = {"type": "attachment", "timestamp": "2026-11-03T17:00:00Z", "attachment": {"type": "queued_command", "prompt": notified, "commandMode": "task-notification"}}
    return [T.user("where are we", timestamp="2026-11-03T16:59:00Z"), queued]


def watch_delivered(thread: str) -> list:
    return [*watch_queued(thread), T.assistant("Answering."), T.user(f"Stop hook feedback:\n{UNANSWERED}", isMeta=True)]


def waiting(replies: str | tuple) -> Input:
    return Input(session_id=HOLDER, command="ls", transcript=delivered(), state=[CompactionState(active=True, communicator=True)], env=DRIVE_CONFIG, commands={f"{SHIPPED_CC_SLACK} dm-owed": replies})


def answering(transcript: list, owed: tuple = (), threads: dict | None = None, **given: object) -> Input:
    replies = json.dumps({"replies": list(owed), "holder_session": HOLDER})
    state = [CompactionState(active=True, communicator=True), RowThreads(threads=threads or {})]
    return Input(session_id=HOLDER, tool=REPLY, tool_input={"title": "Yes, it is on the board", "md": "It is the third card."} | given, transcript=transcript, state=state, env=DRIVE_CONFIG, commands={f"{SHIPPED_CC_SLACK} dm-owed": replies})


def reading(transcript: list, owed: str | tuple, tool: str = f"{CC_SLACK}slack_thread", **given: object) -> Input:
    call = {"command": given.pop("command")} if tool == "Bash" else {"tool": tool, "tool_input": given}
    return Input(session_id=HOLDER, **call, transcript=transcript, state=[CompactionState(active=True, communicator=True)], env=DRIVE_CONFIG, commands={f"{SHIPPED_CC_SLACK} dm-owed": owed})


def owed(replies: str | tuple, *calls: dict, tasks: list = WATCHING) -> Input:
    return Input(session_id=HOLDER, transcript=[T.user("where are we"), *calls, T.assistant("On it.")], state=[CompactionState(active=True, communicator=True)], env=DRIVE_CONFIG, commands={f"{SHIPPED_CC_SLACK} dm-owed": replies}, background_tasks=tasks)


def raised(decide: dict, prompt: str = SIGNAL, **fields: object) -> Input:
    return Input(prompt=prompt, state=[CompactionState(active=True, communicator=True)], env=DRIVE_CONFIG, decide=decide, **fields)


@on(
    Event.UserPromptSubmit,
    only_if=[Communicator()],
    skip_if=[FromSubagent()],
    tests=communicator_tests({
        raised({"system_raised": YES}): Warn(pattern=r"^He asked how a system works or is meant to work\. Before replying, run `lr explain <topic>"),
        raised({"system_raised": NO}): Allow(),
        raised({"system_raised": Refused()}): Allow(),
        raised({"system_raised": YES}, prompt="<task-notification>signal-1 finished</task-notification>"): Allow(),
        raised({"system_raised": YES}, agent_id="a1b2c3"): Allow(),
    }),
)
def owner_words_about_a_system_get_a_page(evt: BaseHookEvent) -> HookResult | None:
    his = OwnerPrompt().check(evt)
    due = his and system_raised(evt)
    with OwnerReturns.mutate(evt) as state:
        state.explain_due = due
        state.asked_why = his and bool(ASKED_WHY.search(evt.user_prompt))
    return evt.context(RAISED) if due else None


def system_raised(evt: BaseHookEvent) -> bool:
    pages = "\n".join(f"- {title}" for title in hosted_titles()) or "(none)"
    match (decision := evt.decide(f"{evt.user_prompt}\n\nHosted one-pagers:\n{pages}", {"system_raised": SYSTEM_RAISED})) and decision.answers["system_raised"]:
        case BinaryAnswer(p_yes=p_yes) if p_yes > 0.5:
            return True
    return False


def hosted_titles() -> list[str]:
    root = read(["git", "config", "ccx.worktreeRoot"])
    worktrees = Path(root.strip()) if root else Path(reqenv.getenv("CLAUDE_CONFIG_DIR") or Path.home() / ".claude") / "worktrees"
    pages = worktrees / "design-docs" / "explainers" / "one-pagers"
    return sorted(html.unescape(found.group(1).strip()) for page in pages.glob("*/index.html") if (found := TITLE.search(page.read_text())))


def explained(reply: str, due: bool = False) -> Input:
    return Input(transcript=[T.user("why did my message vanish"), T.assistant(reply)], state=[CompactionState(active=True, communicator=True), OwnerReturns(explain_due=due)], decide=CALM)


def explain_defect(*calls: dict, defects: str | tuple) -> Input:
    return Input(transcript=[T.user("why did my message vanish"), *calls, T.assistant(WHY)], state=[CompactionState(active=True, communicator=True), OwnerReturns(explain_due=True)], env=DRIVE_CONFIG, commands={EXPLAIN_DEFECTS: defects}, decide=CALM)


def picked(*calls: dict) -> Input:
    return Input(transcript=[T.user("how do we fix the relay"), *calls, T.assistant(PICK)], state=[CompactionState(active=True, communicator=True)], decide=CALM)


def pathed(reply: str) -> Input:
    return Input(transcript=[T.user("where are we"), T.assistant(reply)], state=[CompactionState(active=True, communicator=True)], env=DRIVE_CONFIG, decide=CALM)


def reminded(*calls: dict, decide: dict = REMINDED, asks: str | tuple = "", opening: tuple = (), posted: str | tuple = "") -> Input:
    return Input(transcript=[*opening, T.user(HIS_1_34_AM), *calls, T.assistant("On it.")], state=[CompactionState(active=True, communicator=True)], env=DRIVE_CONFIG, commands={ASKS: asks, "cci post": posted}, decide=decide)


def woken(*said: dict) -> Input:
    return Input(transcript=[*said, T.assistant("Correction to my last line: the rejoin proof failed.")], state=[CompactionState(active=True)], env=DRIVE_CONFIG, commands={ASKS: "", "cci post": ""}, decide=REMINDED)


def ask_to(*to: str) -> str:
    return json.dumps({"seq": 361445, "lane": "owner-relay", "kind": "ask", "to": list(to), "text": "owner:m47 name the row"})


@on(
    Event.Stop,
    only_if=[Communicator()],
    skip_if=[FromSubagent()],
    tests=communicator_tests({
        picked(): Block(pattern=r"^A pick for the owner never sits inline"),
        picked(*T.tool_turn("Monitor", result="armed", description=OWNER_WATCH, timeout_ms=1800000)): Block(pattern=r"^A pick for the owner never sits inline"),
        picked(*T.tool_turn("Monitor", result="armed", description="status read", timeout_ms=1000)): Allow(),
        picked(*T.tool_turn("mcp__plugin_cc-slack_cc-slack__slack_send", result=SENT, channel_id="D0B6Z", text=PICK)): Block(pattern=r"never sits inline.*the `reply` tool, the answers as its `picks`"),
        picked(*T.tool_turn(REPLY, result=REPLIED, title="Land the relay fix tonight?", md="Its checks passed.", picks=["Land it", "Hold it"], recommended="Land it")): Allow(),
        picked(*T.tool_turn(REPLY, result=REPLIED.replace("- slack: https://brook.slack.com/archives/D0B6Z/p1793635190051179", "- slack FAILED: cc-slack dm-status exited 1: channel_not_found"), title="Land the relay fix tonight?", md="Its checks passed.", picks=["Land it", "Hold it"], recommended="Land it")): Block(pattern=r"never sits inline"),
        picked(*T.tool_turn(REPLY, result=REPLIED, title="Land the relay fix tonight?", md="Its checks passed.")): Block(pattern=r"never sits inline"),
        picked(*T.tool_turn("Bash", result=SENT, command=f"cc-slack send --channel D0B6Z --text '{PICK}'")): Block(pattern=r"never sits inline"),
        picked(*T.tool_turn(f"{CC_SLACK}{DM_CARD}", result=CARDED, card=RELAY_CARD)): Allow(),
        picked(*T.tool_turn("Bash", result=CARDED, command=f"cc-slack dm-status --card '{json.dumps(RELAY_CARD)}'")): Allow(),
        picked(*T.tool_turn(f"{CC_SLACK}{DM_CARD}", result="card refused: recommended is not an option", is_error=True, card=RELAY_CARD)): Block(pattern=r"never sits inline"),
        picked(*T.tool_turn(f"{CC_SLACK}{DM_CARD}", result=CARDED, text=PICK)): Block(pattern=r"never sits inline"),
        picked(*T.tool_turn("Bash", result="updated relay-card\nsent: relay-card ts=1793635190.051179 https://brook.slack.com/archives/D0B6Z/p1793635190051179", command="cc-present update-block relay-card --session s --block -")): Allow(),
        picked(*T.tool_turn("Bash", result="updated relay-card", command="cc-present update-block relay-card --block -")): Block(pattern=r"never sits inline"),
        Input(transcript=[T.user("where are we"), T.assistant("Three things landed:\n\n1. The relay fix merged.\n2. The dashboard shows step 2.\n3. CI is green on main.")], state=[CompactionState(active=True, communicator=True)], decide=CALM): Allow(),
        back("Since you were away, three PRs merged.\n\nhttp://dash.brook.ts.net:8840/"): Block(pattern=r"opens with the dashboard link on its first line"),
        back("http://dash.brook.ts.net:8840/\n\nThree PRs merged while you were away."): Allow(),
        back("Since you were away, three PRs merged.", returned=False): Allow(),
        reminded(): Allow(),
        reminded(posted=(1, "bus down")): Allow(),
        reminded(asks=ask_to("meta", "meta-desk-2", "root")): Allow(),
        reminded(asks=ask_to("meta-desk-2")): Allow(),
        reminded(asks=ask_to("root")): Allow(),
        reminded(asks=ask_to("root"), opening=({"type": "attachment", "attachment": {"type": "skill_listing"}},), posted=(1, "bus down")): Allow(),
        reminded(asks=(1, "bus down")): Allow(),
        reminded(*T.tool_turn("Bash", result="R1", command="lr launch meta /drive/meta/brief.md")): Allow(),
        reminded(*T.tool_turn("Bash", result="R1", command="bash ~/.claude/long-running/current/bin/orca-launch.sh /drive/fix-3 brief.md")): Allow(),
        reminded(decide=CALM): Allow(),
        reminded(decide=CALM | {"reminder": Refused()}): Allow(),
        woken(RESUMED, CCI_WAKE, NO_OUTPUT): Allow(),
        woken(RESUMED, NO_OUTPUT, REMINDER_FEEDBACK): Allow(),
        woken(TEAMMATE_WAKE, CCI_WAKE, REMINDER_FEEDBACK): Allow(),
        woken(T.user(HIS_1_34_AM), NO_OUTPUT, REMINDER_FEEDBACK): Allow(),
        pathed("The full update is in /s/brook/update-0312.md."): Block(pattern=r"^The owner never opens a file.*Link the dashboard section"),
        pathed("Notes are at ~/.claude/scratch/relay-notes.md."): Block(pattern=r"never opens a file"),
        pathed("The rows are in `progress-desk/steps.json`."): Block(pattern=r"never opens a file"),
        pathed("The relay row is on http://dash.brook.ts.net:8840/progress-desk/#relay."): Allow(),
        pathed("The fix is in `owner_returns.py`, in `communicator_reply_follows_the_owner_rules`."): Allow(),
        explained(WHY): Allow(),
        explained("Looking into the signal server now.", due=True): Block(pattern=r"^He asked how a system works, so the reply carries a hosted one-pager.*`lr explain <topic>"),
        explained(f"The signal server picks the replica by chat id: {PAGE}", due=True): Allow(),
        explain_defect(*TRIED_EXPLAIN, defects=FILED_DEFECT): Allow(),
        explain_defect(*TRIED_EXPLAIN, defects=(1, "bus down")): Allow(),
        explain_defect(*TRIED_EXPLAIN, defects=""): Block(pattern=r"carries a hosted one-pager"),
        explain_defect(defects=FILED_DEFECT): Block(pattern=r"carries a hosted one-pager"),
    }),
)
def communicator_reply_follows_the_owner_rules(evt: BaseHookEvent) -> HookResult | None:
    route_reminder(evt)
    if any(hands_off(use) for use in evt.ctx.turn.tool_calls.items):
        return None
    reply = AssistantMessage().content(evt) or ""
    problems = [problem for problem in (missing_update(evt, reply), inline_pick(evt, reply), file_path(evt, reply), unexplained(evt, reply)) if problem]
    return evt.block(" ".join(problems)) if problems else None


OWES_HIM = json.dumps({"replies": [OWED_REPLY], "holder_session": HOLDER})
AT_THE_ROOT = OWED_REPLY | {"thread_ts": OWED_REPLY["ts"]}
OWES_THE_ROOT = json.dumps({"replies": [AT_THE_ROOT], "holder_session": HOLDER})


@on(
    Event.Stop,
    only_if=[Communicator()],
    skip_if=[FromSubagent()],
    tests=communicator_tests({
        owed(json.dumps({"replies": [OWED_REPLY], "holder_session": "another-drive"})): Allow(),
        owed(OWES_HIM): Block(pattern=r"^An owner Slack message has no answer[\s\S]*p1793705742180139: A, but as buttons"),
        owed(json.dumps({"replies": [], "holder_session": HOLDER})): Allow(),
        owed(OWES_HIM, *T.tool_turn(OWNER_MARK, result="m447 responded", row="m447", state="responded")): Block(pattern=r"^An owner Slack message has no answer"),
        owed(OWES_HIM, *T.tool_turn("Bash", result="m447 due Nov 3 11:30 PM", command="lr owner mark m447 due --by '11:30 PM' --routed 400803")): Block(),
        owed(OWES_HIM, *T.tool_turn(REPLY, result="Designing.\n\nReply rp; pass this id to edit it on every surface.\n- record: #12@2479ec97\n- ledger: m447 due Nov 3 11:30 PM", title="Vector search", md="Designing.", status="progress", order="m447", by="11:30 PM")): Block(),
        owed(OWES_HIM, *T.tool_turn("Agent", result="started", description="status", prompt="read every lane")): Allow(),
        owed(OWES_HIM, tasks=[*WATCHING, {"id": "a1", "type": "subagent", "status": "running", "description": "answer his thread"}]): Allow(),
        owed(OWES_HIM, tasks=[*WATCHING, {"id": "a2", "type": "subagent", "status": "completed", "description": "answer his thread"}]): Block(pattern=r"^An owner Slack message has no answer"),
        owed(OWES_HIM, *T.tool_turn("Monitor", result="armed", description="status read", timeout_ms=1000)): Block(),
        owed(OWES_HIM, *T.tool_turn("Bash", result="started", command="zsh read.zsh", run_in_background=True)): Block(),
        owed(OWES_HIM, *T.tool_turn("Bash", result="launched", command="lr ask-lane 'how far along is relay-fix-2'")): Block(),
        owed((1, "cc-slack can't reach the daemon")): Allow(),
        owed(OWES_THE_ROOT): Block(pattern=r"^An owner Slack message has no answer"),
        owed(OWES_THE_ROOT, *T.tool_turn(OWNER_MARK, result="m447 responded", row="m447", state="responded")): Allow(),
        owed(OWES_THE_ROOT, *T.tool_turn(OWNER_MARK, result="no row m9", is_error=True, row="m9", state="responded")): Block(),
        owed(OWES_THE_ROOT, *T.tool_turn("Agent", result="started", description="status", prompt="read every lane")): Allow(),
        owed(OWES_THE_ROOT, *T.tool_turn("Monitor", result="armed", description=OWNER_WATCH, timeout_ms=1800000)): Block(),
        Input(session_id=HOLDER, transcript=[T.user("where are we"), T.assistant("On it.")], state=[CompactionState(active=True, communicator=True), OwnerReturns(marked_replies=[AT_THE_ROOT["ts"]])], env=DRIVE_CONFIG, commands={f"{SHIPPED_CC_SLACK} dm-owed": OWES_THE_ROOT}, background_tasks=WATCHING): Allow(),
        owed(json.dumps({"replies": [], "holder_session": HOLDER}), tasks=[]): Block(pattern=r"^The owner's Slack watch is not running.*`lr owner watch`"),
        owed(json.dumps({"replies": [], "holder_session": HOLDER}), tasks=[{"id": "m1", "type": "monitor", "status": "running", "description": "cci subscribe owner-relay"}]): Block(pattern=r"^The owner's Slack watch is not running"),
        owed(json.dumps({"replies": [], "holder_session": HOLDER}), tasks=[{"id": "m2", "type": "shell", "status": "running", "description": "slack", "command": WATCH_COMMAND}]): Allow(),
    }),
)
def owner_slack_waits_hold_the_stop(evt: BaseHookEvent) -> HookResult | None:
    problems = [problem for problem in (unwatched(evt), unanswered_reply(evt)) if problem]
    return evt.block(" ".join(problems)) if problems else None


def unwatched(evt: BaseHookEvent) -> str | None:
    return None if any(task.description == OWNER_WATCH or task.raw.get("command") == WATCH_COMMAND for task in evt.background_tasks) else UNWATCHED


def dm(tool: str = DM_CARD, **given: object) -> Input:
    return Input(tool=f"{CC_SLACK}{tool}", tool_input=given, state=[CompactionState(active=True, communicator=True)])


@on(
    Event.PreToolUse,
    only_if=[Communicator(), Tool("Bash", *WRITE_TOOLS)],
    skip_if=[FromSubagent()],
    tests=communicator_tests({
        dm("slack_send", channel_id="D0B6Z", text=PICK): Warn(pattern=r"^Warning, nothing was blocked: A pick for the owner is never plain text in his Slack DM.*the `reply` tool, the answers as its `picks`"),
        dm(text=PICK): Warn(pattern=r"never plain text"),
        dm("slack_reply", channel_id="D0B6Z", thread_ts="1793635190.051179", text="Reply with a number: 1 keeps one replica, 2 gives each chat its own."): Warn(pattern=r"never plain text"),
        dm(card={"title": "How do we fix the relay?", "body": PICK}): Warn(pattern=r"never plain text"),
        dm(card=RELAY_CARD): Allow(),
        dm(text="A) keeps one replica for every chat. B) gives each chat its own, so one slow chat stalls nobody.", about="pick-buttons:relay"): Allow(),
        dm("slack_send", channel_id="D0B6Z", text="The relay fix merged at 3:10 AM, and the dashboard shows it."): Allow(),
        dm("slack_send", channel_id="C0RELEASES1", text=PICK): Allow(),
        Input(command="cc-slack dm-status 'Reply with A or B: A) one shared replica, B) one per chat'", state=[CompactionState(active=True, communicator=True)]): Warn(pattern=r"never plain text"),
        Input(command=f"cc-slack send --channel D0B6Z --text '{PICK}'", state=[CompactionState(active=True, communicator=True)]): Warn(pattern=r"never plain text"),
        Input(command=f"cc-slack dm-status --card '{json.dumps(RELAY_CARD)}'", state=[CompactionState(active=True, communicator=True)]): Allow(),
        Input(command="cc-slack dm-status 'The relay fix merged at 3:10 AM.'", state=[CompactionState(active=True, communicator=True)]): Allow(),
        Input(command="ls", state=[CompactionState(active=True, communicator=True)]): Allow(),
        dm(card=LAND_CARD): Allow(),
        dm(text=f"Should I land {RELAY_PR} tonight?"): Allow(),
        dm(text=f"{RELAY_PR} merged at 3:10 AM, so the relay keeps one replica per chat."): Allow(),
        Input(command="cc-slack dm-status 'Land PR 2196 tonight, or hold it for the morning build?'", state=[CompactionState(active=True, communicator=True)]): Allow(),
    }),
)
def owner_asks_post_as_cards(evt: BaseHookEvent) -> HookResult | None:
    plain = any(offers_pick(words) and not posted.get("options") for words, posted in dm_posts(evt))
    return evt.context(PLAIN_PICK) if plain else None


def dm_posts(evt: BaseHookEvent) -> list[tuple[str, dict]]:
    if evt.tool_name != "Bash":
        given = evt._tool_input
        to_dm = evt.tool_name.endswith(DM_CARD) or str(given.get("channel_id", "")).startswith("D")
        return [dm_words(str(given.get("text") or ""), given.get("card") or {})] if to_dm and not given.get("about") else []
    posts = []
    for call in evt.command.calls("cc-slack"):
        values = CLI.bind(call).values
        verb, *words = values.get("words") or ("",)
        to_dm = verb == "dm-status" or (verb in ("send", "reply", "edit") and any(str(channel).startswith("D") for channel in values.get("channel", ())))
        if to_dm and not values.get("about"):
            posts.append(dm_words("\n".join([*map(str, values.get("text", ())), *words]), card(next(iter(values.get("card", ())), ""))))
    return posts


def dm_words(text: str, posted: dict) -> tuple[str, dict]:
    return "\n".join([text, str(posted.get("title") or ""), str(posted.get("body") or ""), *map(str, posted.get("options") or [])]), posted


def shipped_cc_slack() -> Path:
    cache = Path(reqenv.getenv("CLAUDE_CONFIG_DIR") or Path.home() / ".claude").expanduser() / CC_SLACK_CACHE
    installs = [path for path in cache.glob("*/bin/cc-slack") if VERSION.fullmatch(path.parents[1].name) and not (path.parents[1] / ".orphaned_at").exists()]
    if not installs:
        raise FileNotFoundError(f"no cc-slack plugin under {cache}")
    return max(installs, key=lambda path: [int(part) for part in path.parents[1].name.split(".")])


def cc_slack(evt: BaseHookEvent, *args: str) -> str | None:
    try:
        out = read([str(shipped_cc_slack()), *args])
    except (OSError, subprocess.SubprocessError) as error:
        return unread_cc_slack(evt, args[0], cause(error))
    return out if out is not None else unread_cc_slack(evt, args[0], "it exited nonzero")


def unread_cc_slack(evt: BaseHookEvent, verb: str, why: str) -> None:
    blocker(evt, f"cc-slack:{verb}", CC_SLACK_UNREAD.format(verb=verb, why=why), to="meta")


@on(
    Event.PostToolUse,
    only_if=[Communicator()],
    skip_if=[FromSubagent()],
    tests=communicator_tests({
        waiting(json.dumps({"replies": [OWED_REPLY], "holder_session": HOLDER})): Warn(pattern=r"^An owner Slack message has waited.*`url` https://\S*p1793705742180139"),
        Input(session_id=HOLDER, command="ls", transcript=[T.user("where are we")], state=[CompactionState(active=True, communicator=True)], env=DRIVE_CONFIG, commands={f"{SHIPPED_CC_SLACK} dm-owed": json.dumps({"replies": [OWED_REPLY], "holder_session": HOLDER})}): Warn(pattern=r"^An owner Slack message has waited"),
        waiting(json.dumps({"replies": [OWED_REPLY], "holder_session": "another-drive"})): Allow(),
        waiting(json.dumps({"replies": [OWED_REPLY | {"ts": f"{time.time() + 3600:.6f}"}], "holder_session": HOLDER})): Allow(),
        waiting(json.dumps({"replies": [], "holder_session": HOLDER})): Allow(),
        waiting((1, "cc-slack can't reach the daemon")): Allow(),
    }),
)
def owner_reply_gets_a_fast_ack(evt: BaseHookEvent) -> HookResult | None:
    try:
        out = read([str(shipped_cc_slack()), "dm-owed"])
    except (OSError, subprocess.SubprocessError):
        return None
    owed = json.loads(out) if out else {"replies": [], "holder_session": ""}
    if owed["holder_session"] != evt.session_id:
        return None
    waited = [reply for reply in owed["replies"] if time.time() - float(reply["ts"]) > ACK_AFTER_SECONDS]
    return evt.warn(UNACKED.format(permalink=waited[0]["permalink"])) if waited else None


def marked(use: object) -> bool:
    if use.result is None or use.result.is_error:
        return False
    if use.call.name == OWNER_MARK:
        return True
    if use.call.name == REPLY:
        return bool(LEDGER_SURFACE.search(use.result.content))
    return use.call.name == "Bash" and any(Path(cmd.argv[0]).name == "lr" and tuple(cmd.argv[1:3]) == ("owner", "mark") for cmd in use.call.command_line if cmd.argv)


def dm_owed(evt: BaseHookEvent) -> dict | None:
    def read_owed() -> dict | None:
        out = cc_slack(evt, "dm-owed")
        return None if out is None else json.loads(out) if out else {"replies": [], "holder_session": ""}

    return reqenv.shared("long_running.dm_owed", read_owed)


def unanswered_reply(evt: BaseHookEvent) -> str | None:
    owed = dm_owed(evt)
    if owed is None or owed["holder_session"] != evt.session_id or not owed["replies"]:
        return None
    calls = evt.ctx.turn.tool_calls.items
    if any(use.call.name in SUBAGENT_TOOLS for use in calls) or any(task.type == "subagent" and task.status == "running" for task in evt.background_tasks):
        return None
    with OwnerReturns.mutate(evt) as state:
        if any(marked(use) for use in calls):
            state.marked_replies = sorted({*state.marked_replies, *(reply["ts"] for reply in owed["replies"])})
        handed_off = any(hands_off(use) for use in calls)
        waiting = [reply for reply in owed["replies"] if reply["thread_ts"] != reply["ts"] or not (handed_off or reply["ts"] in state.marked_replies)]
    return UNANSWERED.format(replies="\n".join(f'{reply["permalink"]}: {reply["text"]}' for reply in waiting)) if waiting else None


@on(
    Event.PreToolUse,
    only_if=[Communicator(), Tool(REPLY)],
    skip_if=[FromSubagent()],
    tests=communicator_tests({
        answering(delivered()): Allow(),
        answering(delivered(thread=OWED_REPLY["ts"])): Allow(),
        answering([T.user("where are we")], owed=(OWED_REPLY,)): Allow(),
        answering(delivered(), owed=(OWED_REPLY,), title="Alert: FlightSQL rejections", status="incident"): Allow(),
        answering([T.user("where are we")], owed=(OWED_REPLY | {"thread_ts": OWED_REPLY["ts"]},)): Allow(),
        answering(delivered(), thread="1793600000.000001"): Allow(),
        answering(delivered(), owed=(OWED_REPLY,), threads={"m12": OWED_REPLY["thread_ts"]}, thread="1793600000.000001", order="m12"): Allow(),
        answering([T.user("where are we")]): Allow(),
        answering(delivered(), threads={"m12": OWED_REPLY["thread_ts"]}, order="m12"): Rewrite(None, thread=OWED_REPLY["thread_ts"]),
        answering(delivered(), threads={"m12": OWED_REPLY["thread_ts"]}, on="line:m12"): Rewrite(None, thread=OWED_REPLY["thread_ts"]),
        answering(delivered(), threads={"m12": OWED_REPLY["thread_ts"]}, order="m11"): Allow(),
        answering(delivered(), owed=(OWED_REPLY,), order="m11"): Allow(),
        answering(delivered(), on="reply:r1"): Allow(),
        answering(watch_queued(OWED_REPLY["thread_ts"]), threads={"m12": OWED_REPLY["thread_ts"]}, order="m12"): Rewrite(None, thread=OWED_REPLY["thread_ts"]),
        answering(watch_queued(OWED_REPLY["thread_ts"])): Allow(),
        answering(watch_delivered(OWED_REPLY["thread_ts"]), threads={"m12": OWED_REPLY["thread_ts"]}, order="m12"): Rewrite(None, thread=OWED_REPLY["thread_ts"]),
        answering(watch_delivered(OWED_REPLY["thread_ts"]), threads={"m12": OWED_REPLY["thread_ts"]}, order="m11"): Allow(),
    }),
)
def answers_go_in_his_thread(evt: BaseHookEvent) -> HookResult | None:
    named = evt.input.raw.get("order") or evt.input.raw.get("on") or ""
    if evt.input.raw.get("thread") or not (thread := RowThreads.load(evt).threads.get(named.removeprefix("line:"))):
        return None
    return evt.rewrite(evt.input.raw | {"thread": thread}, note=IN_THREAD)


class ReadsAnsweredThread:
    def check(self, evt: BaseHookEvent) -> bool:
        if (owed := dm_owed(evt)) is None:
            return True
        held = owed["replies"] if owed["holder_session"] == evt.session_id else []
        threads = {ts for reply in held for ts in (reply["thread_ts"], reply["ts"])} | owner_dm_threads(evt)
        named = json.dumps(evt.input.raw)
        return any(ts in named or f"p{ts.replace('.', '')}" in named for ts in threads)


def owner_dm_threads(evt: BaseHookEvent) -> set[str]:
    heard = owner_messages_heard([*queued_prompts(evt.ctx.t.events), evt.user_prompt or ""])
    return {ts for message in heard if message["channel_id"].startswith("D") for ts in (message["thread_ts"], message["ts"])}


NONE_OWED = json.dumps({"replies": [], "holder_session": HOLDER})
OWED_HERE = json.dumps({"replies": [OWED_REPLY], "holder_session": HOLDER})
hook(  # capt-hook: no-escape the refusal names the lane verb that reads any other thread
    Event.PreToolUse,
    block=True,
    message=THREAD_READ,
    only_if=[Communicator(), Or(Tool(*SLACK_THREAD_TOOLS), runs_verb(CC_SLACK_CLI, ("thread",)))],
    skip_if=[FromSubagent(), ReadsNewestSlackMessage(), ReadsAnsweredThread()],
    transcript_events=REPLY_WINDOW,
    tests=communicator_tests({
        reading(watch_delivered(OWED_REPLY["thread_ts"]), NONE_OWED, channel_id="D0B6Z", thread_ts=OWED_REPLY["thread_ts"], limit=6): Allow(),
        reading(watch_delivered(OWED_REPLY["thread_ts"]), NONE_OWED, channel_id="D0B6Z", thread_ts="1793600000.000001", limit=6): Block(pattern=r"^The communicator reads only the Slack thread it is answering"),
        reading([T.user("where are we")], OWED_HERE, channel_id="D0B6Z", thread_ts=OWED_REPLY["thread_ts"], limit=1000): Allow(),
        reading([T.user("where are we")], OWED_HERE, "Bash", command="cc-slack thread --url D0B6Z/p1793684147131059"): Allow(),
        reading([T.user("where are we")], OWED_HERE, "Bash", command=f"cc-slack thread --url '{OWED_REPLY['permalink']}'"): Allow(),
        reading(delivered(), NONE_OWED, channel_id="D0B6Z", thread_ts=OWED_REPLY["thread_ts"]): Allow(),
        reading([T.user("where are we")], OWED_HERE, channel_id="D0B6Z", thread_ts="1793600000.000001"): Block(pattern=r"^The communicator reads only the Slack thread it is answering.*`lr ask-lane"),
        reading([T.user("where are we")], json.dumps({"replies": [OWED_REPLY], "holder_session": "another-drive"}), channel_id="D0B6Z", thread_ts=OWED_REPLY["thread_ts"]): Block(),
        reading(delivered(), NONE_OWED, "mcp__slack__slack_get_thread", channel="C1", ts="1793600000.000001", limit=20): Block(),
        reading([T.user("where are we")], NONE_OWED, "Bash", command="cc-slack thread --url C0B/p1793600000000001 --limit 1000"): Block(),
        reading([T.user("where are we")], (1, "cc-slack can't reach the daemon"), channel_id="D0B6Z", thread_ts="1793600000.000001"): Allow(),
        reading([T.user("where are we")], NONE_OWED, channel_id="D0B6Z", thread_ts="1793600000.000001", limit=1): Allow(),
        reading([T.user("where are we")], NONE_OWED, "Bash", command="cc-slack thread --url D0B6Z/p1 --limit 1"): Allow(),
    }),
)


def missing_update(evt: BaseHookEvent, reply: str) -> str | None:
    if not OwnerReturns.load(evt).returned or LINK.search(reply.split("\n", 1)[0]):
        return None
    return MISSING


def inline_pick(evt: BaseHookEvent, reply: str) -> str | None:
    return INLINE_PICK if offers_pick(reply) and not any(shown_outside(use) for use in evt.ctx.turn.tool_calls.items) else None


def shown_outside(use: object) -> bool:
    if use.result is None or use.result.is_error or not SLACK_POSTED.search(use.result.content):
        return False
    if use.call.name == REPLY:
        return bool(use.call.raw.get("picks"))
    if use.call.name.rsplit("__", 1)[-1] == DM_CARD:
        return bool((use.call.raw.get("card") or {}).get("options"))
    return use.call.name == "Bash" and any(drawn(cmd.argv) for cmd in use.call.command_line if len(cmd.argv) > 1)


def drawn(argv: list[str]) -> bool:
    match Path(argv[0]).name, argv[1]:
        case "cc-slack", "dm-status":
            return bool(card(next(iter(values(argv, "--card")), "")).get("options"))
        case "cc-present", verb:
            return verb in BOARD_VERBS and "--dry-run" not in argv
    return False


def card(given: str) -> dict:
    if not given:
        return {}
    return json.loads(Path(given[1:]).expanduser().read_text() if given.startswith("@") else given)


def offers_pick(text: str) -> bool:
    return bool(CHOICE.search(text)) or {"A", "B"} <= {letter for found in LETTERED.findall(text) for letter in found if letter}


def route_reminder(evt: BaseHookEvent) -> None:
    if any(launched(use) for use in evt.ctx.turn.tool_calls.items) or not (words := owner_words(evt)):
        return
    match (decision := evt.decide(words, {"reminder": REMINDER})) and decision.answers["reminder"]:
        case BinaryAnswer(p_yes=p_yes) if p_yes > 0.5 and not forwarded(evt):
            text = f"owner correction the relay did not route; make the behaviour automatic. His words: {words[:600]}"
            read(["cci", "post", "--drive", bus_name(drive_entry(evt)), "--lane", COMMUNICATOR_LANE, "--kind", "ask", "--to", "meta-desk", "--text", text])


def hands_off(use: object) -> bool:
    if use.call.name in WAIT_TOOLS:
        return use.call.raw.get("description") != OWNER_WATCH
    return use.call.name == "Bash" and (bool(use.call.raw.get("run_in_background")) or any(cmd.runs("lr", "ask-lane") for cmd in use.call.command_line))


def file_path(evt: BaseHookEvent, reply: str) -> str | None:
    prose = LINK.sub("", reply)
    roots = (*SCRATCH_ROOTS, *((f"{entry['state_dir'].rstrip('/')}/",) if (entry := drive_entry(evt)) else ()))
    return FILE_PATH if any(root in prose for root in roots) else None


def unexplained(evt: BaseHookEvent, reply: str) -> str | None:
    if not reply or not OwnerReturns.load(evt).explain_due or EXPLAINER.search(reply) or explain_failed(evt):
        return None
    return UNEXPLAINED


def turn_started(evt: BaseHookEvent) -> str:
    return next(event.meta.timestamp for event in evt.ctx.turn.events if hasattr(event, "meta")).isoformat()


def explain_failed(evt: BaseHookEvent) -> bool:
    if not any(use.call.name == "Bash" and any(Path(cmd.argv[0]).name == "lr" and "explain" in cmd.argv[1:2] for cmd in use.call.command_line if cmd.argv) for use in evt.ctx.turn.tool_calls.items):
        return False
    since = turn_started(evt)
    out = read(["cci", "tail", "--drive", bus_name(drive_entry(evt)), "--lane", COMMUNICATOR_LANE, "--kind", "defect", "--topic", EXPLAIN_TOPIC, "--since", since, "--budget", str(RECORD_BYTES), "--json"])
    return out is None or bool(out.strip())


def owner_words(evt: BaseHookEvent) -> str:
    events = list(evt.ctx.turn.events)
    typed = [text for event in events if (text := owner_message(event))]
    dms = slack_dms(evt, events)
    held = bool(dms) and (owed := dm_owed(evt)) is not None and owed["holder_session"] == evt.session_id
    return "\n\n".join([*typed, *(dm["text"] for dm in dms if held)])


def forwarded(evt: BaseHookEvent) -> bool:
    since = turn_started(evt)
    out = read(["cci", "tail", "--drive", bus_name(drive_entry(evt)), "--lane", COMMUNICATOR_LANE, "--lane", ROOT_LANE, "--kind", "ask", "--kind", "owner", "--since", since, "--budget", str(RECORD_BYTES), "--json"])
    return out is None or any(to.startswith("meta") for line in out.splitlines() if line.strip() for to in json.loads(line).get("to") or ())


def launched(use: object) -> bool:
    return use.call.name == "Bash" and any(launches(cmd.argv) for cmd in use.call.command_line if cmd.argv)


def launches(argv: Sequence[str]) -> bool:
    names = [Path(word).name for word in argv]
    return "orca-launch.sh" in names or ("lr" in names and names[names.index("lr") + 1 : names.index("lr") + 2] == ["launch"])


def values(argv: Sequence[str], flag: str) -> list[str]:
    return [after for word, after in pairwise(argv) if word == flag] + [word.removeprefix(f"{flag}=") for word in argv if word.startswith(f"{flag}=")]
