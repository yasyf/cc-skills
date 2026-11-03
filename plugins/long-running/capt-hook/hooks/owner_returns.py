from __future__ import annotations

import json
import json
import re
import subprocess
import time
from itertools import pairwise
from collections.abc import Sequence
from pathlib import Path

from spawnllm import Binary, BinaryAnswer, Refused

from captain_hook import Allow, AssistantMessage, BaseHookEvent, Block, Event, FromSubagent, HookResult, Input, T, Tool, Warn, WorkflowState, on, workflow_state

from .compaction_handoff import bus_name, Communicator, COMMUNICATOR_LANE, communicator_tests, CompactionState, drive_entry
from .owner_answers import read, RECORD_BYTES
from .lane_slack_status import CC_SLACK, CLI, WRITE_TOOLS
from .register_capture import slack_dms
from .root_context import OwnerPrompt
from .root_replies import SYSTEM_PREFIXES, owner_text
from .tests.register_fixtures import DRIVE_CONFIG

RETURN_GAP_SECONDS = 60 * 60
STEPS = Path("progress-desk") / "steps.json"
LINK = re.compile(r"https?://\S+")
OWNER_TABLE = re.compile(r"^#+ .+\n(?:.*\n)*?\| Row \| What it delivers \|", re.MULTILINE)
RETURNED = "The owner is back after {minutes} minutes away. Open this reply with the full update: the dashboard link on its first line, then the owner table grouped by feature from `{steps}`."
MISSING = "A reply to the owner's return opens with the full update, and this one lacks {missing}. Rewrite it to open with the dashboard link, then the owner table under a heading per feature."
NO_LINK = "the dashboard link on its first line"
NO_TABLE = "the owner table under a feature heading"
UPDATE = "http://dash.brook.ts.net:8840/\n\n## Durable agents, step Rebuild\n| Row | What it delivers | State | Who is on it right now | What blocks it | What we are doing to unblock it, and by when | What went wrong or slowed it |\n|---|---|---|---|---|---|---|\n| Step 2 | Agents resume | open | fix-3 | nothing recorded | fix-3 lands it | None recorded |"
LETTERED = re.compile(r"^[ \t>*-]*\(?([A-H])[).:]\**\s|(?<![\w(])\(([A-H])\)\s|(?<![\w(])([A-H])\)\**\s", re.MULTILINE)
CHOICE = re.compile(
    r"\b(?i:option) \(?[A-H1-9]\b|(?i:\bpick one\b|\bwhich (?:do you want|would you like|do you prefer|one do you)\b)"
    r"|(?i:\b(?:reply|respond|answer)) with (?:(?i:the |a )?(?i:letter|number)|[A-H1-9]\b)"
)
DM_CARD = "slack_dm_status"
SLACK_POSTED = re.compile(r"\bts\W{1,4}\d{10}\.\d{6}|/archives/\w+/p\d{16}")
BOARD_VERBS = frozenset({"push", "update-block", "ask", "send"})
INLINE_PICK = "A pick for the owner never sits inline or in plain Slack text; he answers with a button. Post it as a `slack_dm_status` card whose `options` are the answers, or as a `cc-present ask` card, then stop again."
PLAIN_PICK = "A pick for the owner is never plain text in his Slack DM; he taps a button, and a thread reply is for notes. Send a `slack_dm_status` card whose `options` are the answers, or a `cc-present ask` card."
QUESTION = re.compile(r"\?(?=\s|$)")
PR_NAMED = re.compile(r"github\.com/[\w.-]+/[\w.-]+/pull/\d+|[\w.-]+/[\w.-]+#\d+|\b(?i:PR|pull request) #?\d+\b")
PR_CARD = "An ask to the owner about a pull request carries its PR card, never only the link. List each PR in the card's `refs` as `{\"url\", \"label\"}`, or ask with `drive.py ask-owner`, which attaches it."
PICK = "Two ways to fix the relay:\n\nA) Keep one shared replica\nB) Give each chat its own\n\nWhich do you want?"
REMINDER = Binary(
    "The owner's words this turn are below; his typos are his, so read through them."
    " Yes when he reminds the assistant of something it should already have done, repeats an instruction or ruling he gave before, or corrects how it works: something missed, forgotten, still happening, done wrong or not followed."
    " No for a new request, a question, an answer to a question, a pick, an approval or thanks that points at nothing the assistant failed to do."
)
UNROUTED_REMINDER = (
    "Every owner reminder or correction spawns a meta tooling lane that makes the behaviour automatic."
    " Before stopping, post one ask to meta naming his ledger row, `owner:m47` plus your one-line order, or launch the lane with `lr launch`."
)
STATUS_QUESTION = Binary(
    "The owner's words this turn are below; his typos are his, so read through them."
    " Yes when he asks how far a lane or a piece of running work has got: its progress or status, whether it is done, where it stands, including a pronoun such as 'that' or 'it' pointing at work named earlier."
    " No for a new request, a pick, or a question about how something works."
)
UNREAD_STATUS = (
    "A status question is answered from the lane's newest record, never from what the lane is for."
    " Read it with `cci tail --lane <lane> -n 1` and answer with what it says, its `#<seq>`, clock time and age in minutes;"
    " if the lane has no record, say so and name when it launched."
)
CCI_TOOLS = "mcp__plugin_cc-inbox_cci__"
SEQ = re.compile(r"#(\d+)\b")
CLOCK = re.compile(r"\b(?:1[0-2]|0?[1-9]):[0-5]\d\s?[AP]M\b", re.IGNORECASE)
FILE_PATH = (
    "The owner never opens a file, so a reply to him names no path under the drive's state dir, a scratch dir or progress-desk."
    " Link the dashboard section that shows it on the tailnet instead, and say what it shows in plain words."
)
SCRATCH_ROOTS = (".claude/scratch/", "progress-desk/")
UNANSWERED = (
    "The owner wrote in Slack and nobody has answered in that thread: {replies}."
    " Reply in each thread with `slack_reply` saying what was done with it, then stop."
)
OWED_REPLY = {"thread_ts": "1793684147.131059", "ts": "1793705742.180139", "text": "A, but as buttons", "permalink": "https://acme.slack.com/archives/D0B6Z/p1793705742180139"}
CALM = {"reminder": BinaryAnswer(p_yes=0.02, confidence=0.95), "status_question": BinaryAnswer(p_yes=0.02, confidence=0.95)}
REMINDED = CALM | {"reminder": BinaryAnswer(p_yes=0.97, confidence=0.94)}
ASKED = CALM | {"status_question": BinaryAnswer(p_yes=0.96, confidence=0.93)}
HOW_FAR = "you told me what the relay fix lane is for already, I asked how far along it is"
NEWEST = "#359771 3:02 AM REPORT relay-fix-2 opened yasyf/cc-skills#2190, CI running"
TAILED = T.tool_turn("Bash", result=NEWEST, command="cci tail --lane relay-fix-2 -n 1")
HIS_1_34_AM = (
    "every tjme i need to remknd you to do skemtjjng, hou need to spawn a meta tooling lane foe thay (includkng tbis)."
    " uou did tjay for the ildate after being away a long tome, so good job, but make sure thay is durable behavior not just this session."
)
ASKS = "cci tail --drive brook-drive --lane owner-relay"
SENT = '{"ok": true, "channel": "D0B6Z", "ts": "1793635190.051179"}'
CARDED = '{"channel_id": "D0B6Z", "thread_ts": "1793635190.051179", "ts": "1793635190.051179"}'
RELAY_CARD = {"title": "How do we fix the relay?", "body": "Each chat keeps its own replica, or they share one.", "options": ["Keep one shared replica", "Give each chat its own"], "recommended": "Give each chat its own"}
RELAY_PR = "https://github.com/yasyf/cc-skills/pull/2196"
LAND_CARD = {"title": "Land the relay fix tonight?", "body": f"Its checks passed: {RELAY_PR}", "options": ["Land", "Hold"], "recommended": "Land"}


@workflow_state("long_running_owner_returns")
class OwnerReturns(WorkflowState):
    prompted_at: float | None = None
    returned: bool = False


def away(minutes: float) -> list:
    return [CompactionState(active=True), OwnerReturns(prompted_at=time.time() - 60 * minutes)]


def back(reply: str, returned: bool = True) -> Input:
    return Input(transcript=[T.user("where are we"), T.assistant(reply)], state=[CompactionState(active=True), OwnerReturns(returned=returned)], decide=CALM)


@on(
    Event.SessionStart | Event.UserPromptSubmit,
    only_if=[Communicator()],
    skip_if=[FromSubagent()],
)
def communicator_holds_the_owners_dm(evt: BaseHookEvent) -> None:
    if me := cc_slack("whoami"):
        cc_slack("follow", "--channel", json.loads(me)["dm_channel_id"], "--session", evt.session_id)


@on(
    Event.UserPromptSubmit,
    only_if=[Communicator()],
    skip_if=[FromSubagent()],
    tests=communicator_tests({
        Input(prompt="where are we", state=away(61), env=DRIVE_CONFIG): Warn(pattern=r"^The owner is back after 61 minutes away\..*`/s/brook/progress-desk/steps\.json`"),
        Input(prompt="where are we", state=away(59), env=DRIVE_CONFIG): Allow(),
        Input(prompt="where are we", state=[CompactionState(active=True)], env=DRIVE_CONFIG): Allow(),
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
    return evt.context(RETURNED.format(minutes=int(gap // 60), steps=Path(drive_entry(evt)["state_dir"]) / STEPS))


def owed(replies: str | tuple) -> Input:
    return Input(transcript=[T.user("where are we"), T.assistant("On it.")], state=[CompactionState(active=True)], commands={"cc-slack dm-owed": replies}, decide=CALM)


def picked(*calls: dict) -> Input:
    return Input(transcript=[T.user("how do we fix the relay"), *calls, T.assistant(PICK)], state=[CompactionState(active=True)], decide=CALM)


def pathed(reply: str) -> Input:
    return Input(transcript=[T.user("where are we"), T.assistant(reply)], state=[CompactionState(active=True)], env=DRIVE_CONFIG, decide=CALM)


def reminded(*calls: dict, decide: dict = REMINDED, asks: str | tuple = "") -> Input:
    return Input(transcript=[T.user(HIS_1_34_AM), *calls, T.assistant("On it.")], state=[CompactionState(active=True)], env=DRIVE_CONFIG, commands={ASKS: asks}, decide=decide)


def ask_to(*to: str) -> str:
    return json.dumps({"seq": 361445, "lane": "owner-relay", "kind": "ask", "to": list(to), "text": "owner:m47 name the row"})


def asked(reply: str, *calls: dict, words: str = HOW_FAR, decide: dict = ASKED) -> Input:
    return Input(transcript=[T.user(words), *calls, T.assistant(reply)], state=[CompactionState(active=True)], decide=decide)


@on(
    Event.Stop,
    only_if=[Communicator()],
    skip_if=[FromSubagent()],
    tests=communicator_tests({
        owed(json.dumps({"replies": [OWED_REPLY]})): Block(pattern=r"^The owner wrote in Slack.*p1793705742180139: A, but as buttons.*`slack_reply`"),
        owed(json.dumps({"replies": []})): Allow(),
        owed((1, "cc-slack can't reach the daemon")): Allow(),
        picked(): Block(pattern=r"^A pick for the owner never sits inline"),
        picked(*T.tool_turn("mcp__plugin_cc-slack_cc-slack__slack_send", result=SENT, channel_id="D0B6Z", text=PICK)): Block(pattern=r"never sits inline.*`slack_dm_status` card whose `options`"),
        picked(*T.tool_turn("Bash", result=SENT, command=f"cc-slack send --channel D0B6Z --text '{PICK}'")): Block(pattern=r"never sits inline"),
        picked(*T.tool_turn(f"{CC_SLACK}{DM_CARD}", result=CARDED, card=RELAY_CARD)): Allow(),
        picked(*T.tool_turn("Bash", result=CARDED, command=f"cc-slack dm-status --card '{json.dumps(RELAY_CARD)}'")): Allow(),
        picked(*T.tool_turn(f"{CC_SLACK}{DM_CARD}", result="card refused: recommended is not an option", is_error=True, card=RELAY_CARD)): Block(pattern=r"never sits inline"),
        picked(*T.tool_turn(f"{CC_SLACK}{DM_CARD}", result=CARDED, text=PICK)): Block(pattern=r"never sits inline"),
        picked(*T.tool_turn("Bash", result="updated relay-card\nsent: relay-card ts=1793635190.051179 https://brook.slack.com/archives/D0B6Z/p1793635190051179", command="cc-present update-block relay-card --session s --block -")): Allow(),
        picked(*T.tool_turn("Bash", result="updated relay-card", command="cc-present update-block relay-card --block -")): Block(pattern=r"never sits inline"),
        Input(transcript=[T.user("where are we"), T.assistant("Three things landed:\n\n1. The relay fix merged.\n2. The dashboard shows step 2.\n3. CI is green on main.")], state=[CompactionState(active=True)], decide=CALM): Allow(),
        back("Since you were away, three PRs merged.\n\n" + UPDATE): Block(pattern=r"lacks the dashboard link on its first line\."),
        back("http://dash.brook.ts.net:8840/\n\nThree PRs merged while you were away."): Block(pattern=r"lacks the owner table under a feature heading\."),
        back(UPDATE): Allow(),
        back("Since you were away, three PRs merged.", returned=False): Allow(),
        reminded(): Block(pattern=r"^Every owner reminder or correction spawns a meta tooling lane.*owner:m47"),
        reminded(asks=ask_to("meta", "meta-desk-2", "root")): Allow(),
        reminded(asks=ask_to("meta-desk-2")): Allow(),
        reminded(asks=ask_to("root")): Block(pattern=r"meta tooling lane"),
        reminded(asks=(1, "bus down")): Allow(),
        reminded(*T.tool_turn("Bash", result="R1", command="lr launch meta /drive/meta/brief.md")): Allow(),
        reminded(*T.tool_turn("Bash", result="R1", command="bash ~/.claude/long-running/current/bin/orca-launch.sh /drive/fix-3 brief.md")): Allow(),
        reminded(decide=CALM): Allow(),
        reminded(decide=CALM | {"reminder": Refused()}): Allow(),
        asked("relay-fix-2 opened its PR and CI is running (#359771, 3:02 AM, 8 minutes ago).", *TAILED): Allow(),
        asked("relay-fix-2 opened its PR and CI is running (#359771, 3:02 AM, 8 minutes ago)."): Block(pattern=r"^A status question is answered from the lane's newest record.*`cci tail --lane <lane> -n 1`"),
        asked("relay-fix-2 rebuilds the relay so each chat keeps its own replica.", *TAILED): Block(pattern=r"never from what the lane is for"),
        asked("relay-fix-2 rebuilds the relay so each chat keeps its own replica.", decide=CALM | {"status_question": BinaryAnswer(p_yes=0.9, confidence=0.3)}): Allow(),
        asked("Launched relay-fix-3 for it.", words="start a second lane on the relay fix", decide=CALM): Allow(),
        asked("relay-fix-2 rebuilds the relay so each chat keeps its own replica.", decide=CALM | {"status_question": Refused()}): Allow(),
        pathed("The full update is in /s/brook/update-0312.md."): Block(pattern=r"^The owner never opens a file.*Link the dashboard section"),
        pathed("Notes are at ~/.claude/scratch/relay-notes.md."): Block(pattern=r"never opens a file"),
        pathed("The rows are in `progress-desk/steps.json`."): Block(pattern=r"never opens a file"),
        pathed("The relay row is on http://dash.brook.ts.net:8840/progress-desk/#relay."): Allow(),
        pathed("The fix is in `owner_returns.py`, in `communicator_reply_follows_the_owner_rules`."): Allow(),
    }),
)
def communicator_reply_follows_the_owner_rules(evt: BaseHookEvent) -> HookResult | None:
    reply = AssistantMessage().content(evt) or ""
    problems = [problem for problem in (unanswered_reply(), missing_update(evt, reply), inline_pick(evt, reply), unrouted_reminder(evt), unread_status(evt, reply), file_path(evt, reply)) if problem]
    return evt.block(" ".join(problems)) if problems else None


def dm(tool: str = DM_CARD, **given: object) -> Input:
    return Input(tool=f"{CC_SLACK}{tool}", tool_input=given, state=[CompactionState(active=True)])


@on(
    Event.PreToolUse,
    only_if=[Communicator(), Tool("Bash", *WRITE_TOOLS)],
    skip_if=[FromSubagent()],
    tests=communicator_tests({
        dm("slack_send", channel_id="D0B6Z", text=PICK): Block(pattern=r"^A pick for the owner is never plain text in his Slack DM.*`slack_dm_status` card whose `options`"),
        dm(text=PICK): Block(pattern=r"never plain text"),
        dm("slack_reply", channel_id="D0B6Z", thread_ts="1793635190.051179", text="Reply with a number: 1 keeps one replica, 2 gives each chat its own."): Block(pattern=r"never plain text"),
        dm(card={"title": "How do we fix the relay?", "body": PICK}): Block(pattern=r"never plain text"),
        dm(card=RELAY_CARD): Allow(),
        dm(text="A) keeps one replica for every chat. B) gives each chat its own, so one slow chat stalls nobody.", about="pick-buttons:relay"): Allow(),
        dm("slack_send", channel_id="D0B6Z", text="The relay fix merged at 3:10 AM, and the dashboard shows it."): Allow(),
        dm("slack_send", channel_id="C0RELEASES1", text=PICK): Allow(),
        Input(command="cc-slack dm-status 'Reply with A or B: A) one shared replica, B) one per chat'", state=[CompactionState(active=True)]): Block(pattern=r"never plain text"),
        Input(command=f"cc-slack send --channel D0B6Z --text '{PICK}'", state=[CompactionState(active=True)]): Block(pattern=r"never plain text"),
        Input(command=f"cc-slack dm-status --card '{json.dumps(RELAY_CARD)}'", state=[CompactionState(active=True)]): Allow(),
        Input(command="cc-slack dm-status 'The relay fix merged at 3:10 AM.'", state=[CompactionState(active=True)]): Allow(),
        Input(command="ls", state=[CompactionState(active=True)]): Allow(),
        dm(card=LAND_CARD): Block(pattern=r"^An ask to the owner about a pull request carries its PR card.*`refs`"),
        dm(card=LAND_CARD | {"refs": [{"url": RELAY_PR, "label": "relay fix"}]}): Allow(),
        dm(text=f"Should I land {RELAY_PR} tonight?"): Block(pattern=r"carries its PR card"),
        dm(text=f"{RELAY_PR} merged at 3:10 AM, so the relay keeps one replica per chat."): Allow(),
        Input(command="cc-slack dm-status 'Land PR 2196 tonight, or hold it for the morning build?'", state=[CompactionState(active=True)]): Block(pattern=r"carries its PR card"),
    }),
)
def owner_asks_post_as_cards(evt: BaseHookEvent) -> HookResult | None:
    problems = dict.fromkeys(problem for words, posted in dm_posts(evt) for problem, found in (
        (PLAIN_PICK, offers_pick(words) and not posted.get("options")),
        (PR_CARD, (offers_pick(words) or QUESTION.search(words)) and PR_NAMED.search(words) and not posted.get("refs")),
    ) if found)
    return evt.block(" ".join(problems)) if problems else None


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


def cc_slack(*args: str) -> str | None:
    try:
        return read(["cc-slack", *args])
    except (OSError, subprocess.SubprocessError):
        return None


def unanswered_reply() -> str | None:
    out = cc_slack("dm-owed")
    replies = json.loads(out)["replies"] if out else []
    return UNANSWERED.format(replies="; ".join(f'{reply["permalink"]}: {reply["text"]}' for reply in replies)) if replies else None


def missing_update(evt: BaseHookEvent, reply: str) -> str | None:
    if not OwnerReturns.load(evt).returned:
        return None
    missing = [gap for gap, found in ((NO_LINK, LINK.search(reply.split("\n", 1)[0])), (NO_TABLE, OWNER_TABLE.search(reply))) if not found]
    return MISSING.format(missing=" and ".join(missing)) if missing else None


def inline_pick(evt: BaseHookEvent, reply: str) -> str | None:
    return INLINE_PICK if offers_pick(reply) and not any(shown_outside(use) for use in evt.ctx.turn.tool_calls.items) else None


def shown_outside(use: object) -> bool:
    if use.result is None or use.result.is_error or not SLACK_POSTED.search(use.result.content):
        return False
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


def unrouted_reminder(evt: BaseHookEvent) -> str | None:
    if any(launched(use) for use in evt.ctx.turn.tool_calls.items) or not (words := owner_words(evt)):
        return None
    match (decision := evt.decide(words, {"reminder": REMINDER})) and decision.answers["reminder"]:
        case BinaryAnswer(p_yes=p_yes) if p_yes > 0.5 and not forwarded(evt):
            return UNROUTED_REMINDER
    return None


def unread_status(evt: BaseHookEvent, reply: str) -> str | None:
    read = {seq for use in evt.ctx.turn.tool_calls.items if reads_records(use) for seq in SEQ.findall(use.result.content)}
    if (CLOCK.search(reply) and read & set(SEQ.findall(reply))) or not (words := owner_words(evt)):
        return None
    match (decision := evt.decide(words, {"status_question": STATUS_QUESTION})) and decision.answers["status_question"]:
        case BinaryAnswer(p_yes=p_yes, confidence=confidence) if p_yes > 0.5 and confidence > 0.5:
            return UNREAD_STATUS
    return None


def reads_records(use: object) -> bool:
    if use.result is None or use.result.is_error:
        return False
    if use.call.name.startswith(CCI_TOOLS):
        return use.call.name != f"{CCI_TOOLS}post"
    return use.call.name == "Bash" and any(Path(cmd.argv[0]).name == "lr" or (Path(cmd.argv[0]).name == "cci" and cmd.argv[1:2] != ["post"]) for cmd in use.call.command_line if cmd.argv)


def file_path(evt: BaseHookEvent, reply: str) -> str | None:
    prose = LINK.sub("", reply)
    roots = (*SCRATCH_ROOTS, *((f"{entry['state_dir'].rstrip('/')}/",) if (entry := drive_entry(evt)) else ()))
    return FILE_PATH if any(root in prose for root in roots) else None


def owner_words(evt: BaseHookEvent) -> str:
    events = list(evt.ctx.turn.events)
    typed = [text for event in events if (text := owner_text(event)) and not text.lstrip().startswith(SYSTEM_PREFIXES)]
    return "\n\n".join([*typed, *(dm["text"] for dm in slack_dms(evt, events))])


def forwarded(evt: BaseHookEvent) -> bool:
    since = next(iter(evt.ctx.turn.events)).meta.timestamp.isoformat()
    out = read(["cci", "tail", "--drive", bus_name(drive_entry(evt)), "--lane", COMMUNICATOR_LANE, "--kind", "ask", "--since", since, "--budget", str(RECORD_BYTES), "--json"])
    return out is None or any(to.startswith("meta") for line in out.splitlines() if line.strip() for to in json.loads(line).get("to") or ())


def launched(use: object) -> bool:
    return use.call.name == "Bash" and any(launches(cmd.argv) for cmd in use.call.command_line if cmd.argv)


def launches(argv: Sequence[str]) -> bool:
    names = [Path(word).name for word in argv]
    return "orca-launch.sh" in names or ("lr" in names and names[names.index("lr") + 1 : names.index("lr") + 2] == ["launch"])


def values(argv: Sequence[str], flag: str) -> list[str]:
    return [after for word, after in pairwise(argv) if word == flag] + [word.removeprefix(f"{flag}=") for word in argv if word.startswith(f"{flag}=")]
