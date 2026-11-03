from __future__ import annotations

import re
import time
from itertools import pairwise
from collections.abc import Sequence
from pathlib import Path

from spawnllm import Binary, BinaryAnswer, Refused

from captain_hook import Allow, AssistantMessage, BaseHookEvent, Block, Event, FromSubagent, HookResult, Input, T, Warn, WorkflowState, on, workflow_state

from .compaction_handoff import Communicator, communicator_tests, CompactionState, drive_entry
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
CHOICE = re.compile(r"\b(?i:option) \(?[A-H1-9]\b|(?i:\bpick one\b|\bwhich (?:do you want|would you like|do you prefer|one do you)\b)")
SLACK_TOOLS = frozenset({"slack_send", "slack_reply", "slack_send_message"})
SLACK_TS = re.compile(r"\bts\W{1,4}\d{10}\.\d{6}")
BOARD_VERBS = frozenset({"push", "update-block", "ask"})
INLINE_PICK = "A pick for the owner never sits inline in this window. Post the options at his Slack DM root with `slack_send`, or draw them as a board card with `cc-present push` or `cc-present update-block`, then stop again."
PICK = "Two ways to fix the relay:\n\nA) Keep one shared replica\nB) Give each chat its own\n\nWhich do you want?"
REMINDER = Binary(
    "The owner's words this turn are below; his typos are his, so read through them."
    " Yes when he reminds the assistant of something it should already have done, repeats an instruction or ruling he gave before, or corrects how it works: something missed, forgotten, still happening, done wrong or not followed."
    " No for a new request, a question, an answer to a question, a pick, an approval or thanks that points at nothing the assistant failed to do."
)
UNROUTED_REMINDER = (
    "Every owner reminder or correction spawns a meta tooling lane that makes the behaviour automatic."
    " Before stopping, forward his words verbatim with their time as `cci post --lane owner-relay --kind ask --to meta`, or launch the lane with `lr launch`."
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
FORWARDED = "cci post --lane owner-relay --kind ask --to meta-desk-1 --text '1:34 AM, verbatim: every tjme i need to remknd you'"
MCP_POST_SUFFIX = "cc-inbox_cci__post"
MCP_POST = f"mcp__plugin_{MCP_POST_SUFFIX}"
SENT = '{"ok": true, "channel": "D0B6Z", "ts": "1793635190.051179"}'


@workflow_state("long_running_owner_returns")
class OwnerReturns(WorkflowState):
    prompted_at: float | None = None
    returned: bool = False


def away(minutes: float) -> list:
    return [CompactionState(active=True), OwnerReturns(prompted_at=time.time() - 60 * minutes)]


def back(reply: str, returned: bool = True) -> Input:
    return Input(transcript=[T.user("where are we"), T.assistant(reply)], state=[CompactionState(active=True), OwnerReturns(returned=returned)], decide=CALM)


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


def picked(*calls: dict) -> Input:
    return Input(transcript=[T.user("how do we fix the relay"), *calls, T.assistant(PICK)], state=[CompactionState(active=True)], decide=CALM)


def reminded(*calls: dict, decide: dict = REMINDED) -> Input:
    return Input(transcript=[T.user(HIS_1_34_AM), *calls, T.assistant("On it.")], state=[CompactionState(active=True)], decide=decide)


def asked(reply: str, *calls: dict, words: str = HOW_FAR, decide: dict = ASKED) -> Input:
    return Input(transcript=[T.user(words), *calls, T.assistant(reply)], state=[CompactionState(active=True)], decide=decide)


@on(
    Event.Stop,
    only_if=[Communicator()],
    skip_if=[FromSubagent()],
    tests=communicator_tests({
        picked(): Block(pattern=r"^A pick for the owner never sits inline"),
        picked(*T.tool_turn("mcp__plugin_cc-slack_cc-slack__slack_send", result=SENT, channel_id="D0B6Z", text=PICK)): Allow(),
        picked(*T.tool_turn("mcp__plugin_cc-slack_cc-slack__slack_send", result="channel_not_found", is_error=True, channel_id="D0B6Z", text=PICK)): Block(pattern=r"never sits inline"),
        picked(*T.tool_turn("Bash", result="updated relay-card", command="cc-present update-block relay-card --session s --block -")): Allow(),
        Input(transcript=[T.user("where are we"), T.assistant("Three things landed:\n\n1. The relay fix merged.\n2. The dashboard shows step 2.\n3. CI is green on main.")], state=[CompactionState(active=True)], decide=CALM): Allow(),
        back("Since you were away, three PRs merged.\n\n" + UPDATE): Block(pattern=r"lacks the dashboard link on its first line\."),
        back("http://dash.brook.ts.net:8840/\n\nThree PRs merged while you were away."): Block(pattern=r"lacks the owner table under a feature heading\."),
        back(UPDATE): Allow(),
        back("Since you were away, three PRs merged.", returned=False): Allow(),
        reminded(): Block(pattern=r"^Every owner reminder or correction spawns a meta tooling lane.*--to meta"),
        reminded(*T.tool_turn("Bash", result="#1", command="cci post --lane owner-relay --kind go --to meta-desk-1 --text x")): Block(pattern=r"meta tooling lane"),
        reminded(*T.tool_turn("Bash", result="#1", command=FORWARDED)): Allow(),
        reminded(*T.tool_turn("Bash", result="#1", command="cci post --lane owner-relay --kind=ask --to=root,meta --text x")): Allow(),
        reminded(*T.tool_turn("Bash", result="#1", command="cci post --lane owner-relay --kind ask --to meta,meta-desk-1 --text x")): Allow(),
        reminded(*T.tool_turn(MCP_POST, result="#359589", lane="owner-relay", kind="ask", to=["meta", "meta-desk-1", "root"], text="3:08 AM, verbatim: x")): Allow(),
        reminded(*T.tool_turn(MCP_POST, result="#1", lane="owner-relay", kind="ask", to=["meta-desk-2"], text="x")): Allow(),
        reminded(*T.tool_turn(MCP_POST, result="#1", lane="owner-relay", kind="ask", to=["root"], text="x")): Block(pattern=r"meta tooling lane"),
        reminded(*T.tool_turn(MCP_POST, result="#1", lane="owner-relay", kind="report", to=["meta-desk-1"], text="x")): Block(pattern=r"meta tooling lane"),
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
    }),
)
def communicator_reply_follows_the_owner_rules(evt: BaseHookEvent) -> HookResult | None:
    reply = AssistantMessage().content(evt) or ""
    problems = [problem for problem in (missing_update(evt, reply), inline_pick(evt, reply), unrouted_reminder(evt), unread_status(evt, reply)) if problem]
    return evt.block(" ".join(problems)) if problems else None


def missing_update(evt: BaseHookEvent, reply: str) -> str | None:
    if not OwnerReturns.load(evt).returned:
        return None
    missing = [gap for gap, found in ((NO_LINK, LINK.search(reply.split("\n", 1)[0])), (NO_TABLE, OWNER_TABLE.search(reply))) if not found]
    return MISSING.format(missing=" and ".join(missing)) if missing else None


def inline_pick(evt: BaseHookEvent, reply: str) -> str | None:
    offers = CHOICE.search(reply) or {"A", "B"} <= {letter for found in LETTERED.findall(reply) for letter in found if letter}
    return INLINE_PICK if offers and not any(shown_outside(use) for use in evt.ctx.turn.tool_calls.items) else None


def shown_outside(use: object) -> bool:
    if use.result is None or use.result.is_error:
        return False
    if use.call.name.rsplit("__", 1)[-1] in SLACK_TOOLS:
        return bool(SLACK_TS.search(use.result.content))
    return use.call.name == "Bash" and any(drawn(cmd.argv, use.result.content) for cmd in use.call.command_line if len(cmd.argv) > 1)


def drawn(argv: list[str], result: str) -> bool:
    match Path(argv[0]).name, argv[1]:
        case "cc-slack", "send" | "reply":
            return bool(SLACK_TS.search(result))
        case "cc-present", verb:
            return verb in BOARD_VERBS and "--dry-run" not in argv
        case "cci", "post":
            return any(flag == "--attr" and value.startswith("card=") and value != "card=" for flag, value in pairwise(argv))
    return False


def unrouted_reminder(evt: BaseHookEvent) -> str | None:
    if any(forwarded(use) for use in evt.ctx.turn.tool_calls.items) or not (words := owner_words(evt)):
        return None
    match (decision := evt.decide(words, {"reminder": REMINDER})) and decision.answers["reminder"]:
        case BinaryAnswer(p_yes=p_yes) if p_yes > 0.5:
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


def owner_words(evt: BaseHookEvent) -> str:
    events = list(evt.ctx.turn.events)
    typed = [text for event in events if (text := owner_text(event)) and not text.lstrip().startswith(SYSTEM_PREFIXES)]
    return "\n\n".join([*typed, *(dm["text"] for dm in slack_dms(evt, events))])


def forwarded(use: object) -> bool:
    if use.call.name.endswith(MCP_POST_SUFFIX):
        raw = use.call.raw
        return raw.get("lane") == "owner-relay" and raw.get("kind") == "ask" and any(to.startswith("meta") for to in raw.get("to") or ())
    return use.call.name == "Bash" and any(routes_to_meta(cmd.argv) for cmd in use.call.command_line if cmd.argv)


def routes_to_meta(argv: Sequence[str]) -> bool:
    names = [Path(word).name for word in argv]
    if "orca-launch.sh" in names or ("lr" in names and names[names.index("lr") + 1 : names.index("lr") + 2] == ["launch"]):
        return True
    return names[0] == "cci" and "post" in argv and "ask" in values(argv, "--kind") and any(to.startswith("meta") for value in values(argv, "--to") for to in value.split(","))


def values(argv: Sequence[str], flag: str) -> list[str]:
    return [after for word, after in pairwise(argv) if word == flag] + [word.removeprefix(f"{flag}=") for word in argv if word.startswith(f"{flag}=")]
