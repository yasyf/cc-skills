from __future__ import annotations

from pathlib import Path

from captain_hook import Allow, AssistantMessage, BaseHookEvent, Block, Event, FromSubagent, hook, HookResult, Input, llm_gate, on, T, Tool, Warn

from .compaction_handoff import Communicator, communicator_tests, CompactionState
from .root_replies import read_call

PROMPT = """Judge only the message in <assistant_message>, the reply that ends the assistant's turn in the owner's chat.
Block when nothing in it tells the owner, in second person, what changed for him or what he must decide: a message that is only ledger rows or ledger marks, hook or tooling mechanics, acknowledgements of lanes, workers or records, or a "nothing new" or "no change" line.
Do not block a message that tells the owner something that changed for him or puts a decision to him, even when it also carries one of those lines."""

REPLY = "The communicator's chat carries only what changed for the owner or what he must decide, in second person. End the turn with no text, or rewrite the reply to that change or decision."
RECORDS_ONLY = "This wake carries only cci records. A record that changes nothing for the owner and puts no decision to him ends this turn with no text at all; never tell him that all is in hand or that nothing is new."
CCI_CHANNEL = '<channel source="plugin:cc-inbox:cci"'
MONITOR = [{"id": "m1", "type": "monitor", "status": "running", "description": "cci subscribe owner-relay"}]
READS = "Two Bash reads in a row with no subagent between them. Hand the rest of this read to `Agent` with `Explore` and `model: sonnet`, and keep only its answer."

ACTIVE = [CompactionState(active=True)]


def closing(text: str) -> list:
    return [T.user("where are we"), T.assistant(text)]


def turn(*calls) -> list:
    return [T.user("what's the dashboard url"), T.assistant(*calls)]


class BashRead:
    def check(self, evt: BaseHookEvent) -> bool:
        return read_call("Bash", [(call.name, *call.args) for call in evt.command.calls()])


class FollowsBashRead:
    def check(self, evt: BaseHookEvent) -> bool:
        calls = [use.call for use in evt.ctx.turn.tool_calls.items if use.ref.tool_use_id != evt.tool_use_id and use.call.name in {"Bash", "Agent", "Task"}]
        return bool(calls) and calls[-1].name == "Bash" and read_call("Bash", [(Path(cmd.argv[0]).name, *cmd.argv[1:]) for cmd in calls[-1].command_line if cmd.argv])


llm_gate(
    PROMPT,
    message=REPLY,
    label="communicator_replies_carry_owner_news",
    contexts=[AssistantMessage()],
    only_if=[Communicator()],
    skip_if=[FromSubagent()],
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
    *channels, rest = (evt.user_prompt or "").split("</channel>")
    if rest.strip() or not channels or not all(channel.strip().startswith(CCI_CHANNEL) for channel in channels):
        return None
    return evt.context(RECORDS_ONLY)


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
