from __future__ import annotations

import re
import subprocess
from pathlib import Path

from cc_transcript.models import AssistantEvent, AttachmentEvent, UserEvent
from captain_hook import Allow, AssistantMessage, BaseHookEvent, Block, Event, FromSubagent, hook, HookResult, Input, llm_gate, on, T, Tool, Warn
from captain_hook.util import reqenv

from .cci_mcp_posts import POST_TOOL
from .compaction_handoff import bus_name, CCN_TIMEOUT_SECONDS, Communicator, COMMUNICATOR_LANE, communicator_tests, CompactionState, drive_entry
from .meta_records import posts
from .root_replies import owner_text, read_call
from .tests.register_fixtures import DRIVE_CONFIG

PROMPT = """Judge only the message in <assistant_message>, the reply that ends the assistant's turn in the owner's chat.
Block when nothing in it tells the owner, in second person, what changed for him or what he must decide: a message that is only ledger rows or ledger marks, hook or tooling mechanics, acknowledgements of lanes, workers or records, or a "nothing new" or "no change" line.
Do not block a message that tells the owner something that changed for him or puts a decision to him, even when it also carries one of those lines."""

REPLY = "The communicator's chat carries only what changed for the owner or what he must decide, in second person. End the turn with no text, or rewrite the reply to that change or decision."
RECORDS_ONLY = "This wake carries only cci records. A record that changes nothing for the owner and puts no decision to him ends this turn with no text at all, even when Claude Code says he has not heard from you in a while; never tell him that all is in hand or that nothing is new."
CCI_CHANNEL = '<channel source="plugin:cc-inbox:cci"'
MONITOR = [{"id": "m1", "type": "monitor", "status": "running", "description": "cci subscribe owner-relay"}]
WAKE = f'{CCI_CHANNEL} seq="359771">#359771 board-watch owner: the owner picked “Land” on merge-prs</channel>'
REFUSED = {"type": "attachment", "timestamp": "2026-01-15T20:19:00Z", "attachment": {"type": "hook_blocking_error", "hookName": "Stop", "blockingError": {"blockingError": REPLY, "command": "capt-hook"}}}
NOOP = "A do-nothing Bash call ends no turn, and the owner sees every one. A wake with nothing for him ends here: no text and no tool call, even when Claude Code says he has not heard from you in a while."
CHORE_DESK = "meta-desk-3"
CHORE_RUNS, CHORE_ASKS = "chore-runs", "chore-asks"
CHORE_VERBS = frozenset({"lr", "cci"})
TAIL = "cci tail --lane relay-fix-2 -n 1"
TAIL_SHAPE = "cci tail --lane -n"
READS = "Two Bash reads in a row with no subagent between them. Hand the rest of this read to `Agent` with `Explore` and `model: sonnet`, and keep only its answer."
DESIGN_ASK = re.compile(r"\b(?:rethink\w*|redesign\w*|first[- ]principles?|bring (?:me|him) options|options? (?:card|covering)|design (?:order|ask|card))\b", re.IGNORECASE)
CARVE_OUT = re.compile(r"\bships? (?:\w+ ){0,3}now\b|\bcan ship\b|\bsettle[sd]?\b", re.IGNORECASE)
QUOTED = re.compile(r"\"[^\"]*\"|“[^”]*”")
CARVED = (
    "This record carves work that ships now or is settled out of his design order, which is a routing defect:"
    " route the concrete rule onto the design's options card as an input, and say that nothing in that subsystem ships until his pick except the incident fix and headroom."
)
ROUTED = "cci post --lane owner-relay --kind decide --to journal-evict-fix --text '{text}'"

ACTIVE = [CompactionState(active=True, communicator=True)]


def closing(text: str) -> list:
    return [T.user("where are we"), T.assistant(text)]


def turn(*calls) -> list:
    return [T.user("what's the dashboard url"), T.assistant(*calls)]


def records_only(prompt: str) -> bool:
    *channels, rest = prompt.split("</channel>")
    return not rest.strip() and bool(channels) and all(channel.strip().startswith(CCI_CHANNEL) for channel in channels)


class QuietRecordsWake:
    def check(self, evt: BaseHookEvent) -> bool:
        events = list(evt.ctx.turn.events)
        said = [text for event in events if (text := owner_text(event)) and (records_only(text) or not event.meta.is_meta)]
        return bool(said) and all(records_only(text) for text in said) and not replied(events)


def replied(events: list) -> bool:
    for event in reversed(events):
        if isinstance(event, AssistantEvent) and event.text.strip():
            return True
        if isinstance(event, UserEvent) or (isinstance(event, AttachmentEvent) and event.attachment_type == "hook_blocking_error"):
            return False
    return False


class BashRead:
    def check(self, evt: BaseHookEvent) -> bool:
        return read_call("Bash", [(call.name, *call.args) for call in evt.command.calls()])


class FollowsBashRead:
    def check(self, evt: BaseHookEvent) -> bool:
        calls = [use.call for use in evt.ctx.turn.tool_calls.items if use.ref.tool_use_id != evt.tool_use_id and use.call.name in {"Bash", "Agent", "Task"}]
        return bool(calls) and calls[-1].name == "Bash" and read_call("Bash", [(Path(cmd.argv[0]).name, *cmd.argv[1:]) for cmd in calls[-1].command_line if cmd.argv])


class NoOpBash:
    def check(self, evt: BaseHookEvent) -> bool:
        return [(call.name, *call.args) for call in evt.command.calls()] in ([("true",)], [(":",)])


llm_gate(
    PROMPT,
    message=REPLY,
    label="communicator_replies_carry_owner_news",
    contexts=[AssistantMessage()],
    only_if=[Communicator()],
    skip_if=[FromSubagent(), QuietRecordsWake()],
    once_per_turn=False,
    guards_waiting=False,
    max_fires=None,
    transcript=False,
    agent=False,
    backend="jev",
    events=Event.Stop,
    tests=communicator_tests({
        Input(transcript=closing("Ledger: 9:10 PM marked responded, 9:12 PM marked addressed."), state=ACTIVE): Block(pattern=r"^The communicator's chat carries only"),
        Input(transcript=closing("The register_capture hook fired on the Stop event and wrote the capture row."), state=ACTIVE): Block(),
        Input(transcript=closing("Seen: fix-3 acked the coordinator's go."), state=ACTIVE): Block(),
        Input(transcript=closing("Nothing new."), state=ACTIVE): Block(),
        Input(transcript=closing("All in hand; nothing new for you."), state=ACTIVE, background_tasks=MONITOR): Block(),
        Input(transcript=closing("Your signal fix is live: chats you open now load their history."), state=ACTIVE, llm={"block": False}): Allow(),
        Input(transcript=closing("You need to pick whether the restore runs tonight or tomorrow."), state=ACTIVE, llm={"block": False}): Allow(),
        Input(transcript=turn(T.tool("Bash", command="cci post --lane owner-relay --kind ask --to root --text x")), state=ACTIVE): Allow(),
        Input(transcript=closing("Nothing new."), agent_id="a1b2c3", state=ACTIVE): Allow(),
        Input(transcript=[T.user(WAKE), T.assistant("Tap noted: merge-prs is with the coordinator."), REFUSED, T.assistant("")], state=ACTIVE): Allow(),
        Input(transcript=[T.user(WAKE), T.assistant("")], state=ACTIVE): Allow(),
        Input(transcript=[T.user(WAKE), T.assistant("All in hand; nothing new for you.")], state=ACTIVE): Block(),
        Input(transcript=[T.user("how is the relay fix going"), T.assistant("Tap noted: merge-prs is with the coordinator."), REFUSED, T.assistant("")], state=ACTIVE): Block(pattern=r"^The communicator's chat carries only"),
    }),
)

@on(
    Event.UserPromptSubmit,
    only_if=[Communicator()],
    skip_if=[FromSubagent()],
    tests=communicator_tests({
        Input(prompt=f'{CCI_CHANNEL} seq="359771">#359771 relay-fix-2 report: CI green</channel>', state=ACTIVE): Warn(pattern=r"^This wake carries only cci records\..*no text at all"),
        Input(prompt=f'{CCI_CHANNEL} seq="359771">#359771 relay-fix-2 report: CI green</channel>\n{CCI_CHANNEL} seq="359772">#359772 fix-3 opened a PR</channel>', state=ACTIVE): Warn(),
        Input(prompt=f'{CCI_CHANNEL} seq="359771">#359771 relay-fix-2 report: CI green</channel>\nhow is the relay fix going', state=ACTIVE): Allow(),
        Input(prompt="how is the relay fix going", state=ACTIVE): Allow(),
        Input(prompt=f'{CCI_CHANNEL} seq="359771">#359771 relay-fix-2 report: CI green</channel>', state=ACTIVE, agent_id="a1b2c3"): Allow(),
    }),
)
def records_only_wake_ends_quiet(evt: BaseHookEvent) -> HookResult | None:
    return evt.context(RECORDS_ONLY) if records_only(evt.user_prompt or "") else None


hook(
    Event.PreToolUse,
    message=READS,
    only_if=[Communicator(), Tool("Bash"), BashRead(), FollowsBashRead()],
    skip_if=[FromSubagent()],
    tests=communicator_tests({
        Input(command="cat /drive/progress-desk/steps.json", state=ACTIVE, transcript=turn(T.tool("Bash", command="rg -n url /drive/live-dashboard"))): Warn(pattern=r"^Two Bash reads in a row"),
        Input(command="cat /drive/progress-desk/steps.json", state=ACTIVE, transcript=turn(T.tool("Bash", command="rg -n url /drive/live-dashboard"), T.tool("Agent", prompt="Find the dashboard URL."))): Allow(),
        Input(command="cat /drive/progress-desk/steps.json", state=ACTIVE, transcript=turn()): Allow(),
        Input(command="cci post --lane owner-relay --kind ask --to root --text x", state=ACTIVE, transcript=turn(T.tool("Bash", command="rg -n url /drive/live-dashboard"))): Allow(),
        Input(command="cat /drive/progress-desk/steps.json", agent_id="a1b2c3", state=ACTIVE, transcript=turn(T.tool("Bash", command="rg -n url /drive/live-dashboard"))): Allow(),
    }),
)

hook(
    Event.PreToolUse,
    block=True,
    message=NOOP,
    only_if=[Communicator(), Tool("Bash"), NoOpBash()],
    skip_if=[FromSubagent()],
    tests=communicator_tests({
        Input(command="true", state=ACTIVE, transcript=turn()): Block(pattern=r"^A do-nothing Bash call"),
        Input(command=":", state=ACTIVE, transcript=turn()): Block(),
        Input(command="true && cci post --lane owner-relay --kind note --text x", state=ACTIVE, transcript=turn()): Allow(),
        Input(command="cci post --lane owner-relay --kind note --text true", state=ACTIVE, transcript=turn()): Allow(),
        Input(command="true", agent_id="a1b2c3", state=ACTIVE, transcript=turn()): Allow(),
    }),
)


def chores(evt: BaseHookEvent) -> list[str]:
    return [
        " ".join([call.name, *[arg for arg in call.args if not arg.startswith("-")][:1], *sorted({arg.split("=", 1)[0] for arg in call.args if arg.startswith("-")})])
        for call in evt.command.calls()
        if call.name in CHORE_VERBS and "owner" not in call.args[:1]
    ]


def ask_for_a_verb(evt: BaseHookEvent, shape: str) -> None:
    if not (entry := drive_entry(evt)):
        return
    topic = "chore:" + re.sub(r"\W+", "-", shape).strip("-")
    text = f"The communicator ran `{shape}` twice this session; give it one verb for this chore."
    argv = ["cci", "post", "--drive", bus_name(entry), "--lane", COMMUNICATOR_LANE, "--kind", "ask", "--to", CHORE_DESK, "--topic", topic, "--text", text]
    try:
        subprocess.run(argv, capture_output=True, text=True, timeout=CCN_TIMEOUT_SECONDS, env=dict(reqenv.env_map()), check=False)
    except (OSError, subprocess.TimeoutExpired):
        return


@on(
    Event.PostToolUse,
    only_if=[Communicator(), Tool("Bash")],
    skip_if=[FromSubagent()],
    tests=communicator_tests({
        Input(command=TAIL, state=ACTIVE, env=DRIVE_CONFIG, commands={"cci post": ""}): Allow(),
        Input(command=TAIL, state=ACTIVE, env=DRIVE_CONFIG, seen={CHORE_RUNS: [TAIL_SHAPE]}, commands={"cci post": ""}): Allow(),
        Input(command=TAIL, state=ACTIVE, env=DRIVE_CONFIG, seen={CHORE_RUNS: [TAIL_SHAPE]}, commands={"cci post": (1, "bus down")}): Allow(),
        Input(command=f"{TAIL} && cci tail --lane fix-3 -n 1", state=ACTIVE, env=DRIVE_CONFIG, commands={"cci post": ""}): Allow(),
        Input(command="ls /drive", state=ACTIVE, env=DRIVE_CONFIG): Allow(),
        Input(command="lr owner mark m1 addressed", state=ACTIVE, env=DRIVE_CONFIG, seen={CHORE_RUNS: ["lr owner"]}): Allow(),
    }),
)
def repeated_chores_ask_for_a_verb(evt: BaseHookEvent) -> None:
    for shape in chores(evt):
        if not evt.ctx.s.once(shape, scope=CHORE_RUNS) and evt.ctx.s.once(shape, scope=CHORE_ASKS):
            ask_for_a_verb(evt, shape)


@on(  # capt-hook: no-escape the owner ordered that a design order's rule never reaches a lane as work that ships ahead of his pick
    Event.PreToolUse,
    only_if=[Communicator(), Tool("Bash", POST_TOOL)],
    skip_if=[FromSubagent()],
    tests=communicator_tests({
        Input(command=ROUTED.format(text="Owner m469: bring him options covering all angles on journal storage; clean finish cleanup is settled and ships now."), state=ACTIVE): Block(pattern=r"^This record carves work that ships now"),
        Input(command=ROUTED.format(text="Owner m469: bring him options covering all angles on journal storage; nothing there ships until his pick except the incident fix and headroom."), state=ACTIVE): Allow(),
        Input(command=ROUTED.format(text='Owner m476: a first-principles design of journal storage; this supersedes "clean finish cleanup ships now".'), state=ACTIVE): Allow(),
        Input(command=ROUTED.format(text="Owner m480: the relay fix is settled and ships now."), state=ACTIVE): Allow(),
        Input(command=ROUTED.format(text="Owner m469: rethink journal storage; the cleanup ships now."), agent_id="a1b2c3", state=ACTIVE): Allow(),
    }),
)
def design_orders_carve_out_nothing(evt: BaseHookEvent) -> HookResult | None:
    carved = any(DESIGN_ASK.search(text) and CARVE_OUT.search(QUOTED.sub("", text)) for _, _, _, text, _, _ in posts(evt))
    return evt.block(CARVED) if carved else None
