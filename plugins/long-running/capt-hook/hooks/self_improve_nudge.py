from __future__ import annotations

from captain_hook import Allow, AssistantMessage, Event, FromSubagent, Input, T, Warn, llm_nudge

from .compaction_handoff import Communicator, communicator_tests, CompactionState

PROMPT = """Judge only the message in <assistant_message>, which the assistant just sent the owner.
Fire only when the message itself reports, as its own news, something that went wrong (a failure, a defect, a miss, a refused or broken tool, a repeated owner ruling, a worker or lane that did not deliver) and does not also say what is being done so it does not recur: the mechanism, the tooling or process change, and its PR, install or owner.
Do not fire on an acknowledgement, a duplicate notice, a pointer to a record, or a relay or quote of another lane's or the owner's report, even when the relayed or quoted text names a failure.
Do not fire when the message reports nothing wrong, or when it names the change that stops the problem recurring and who carries it."""

MESSAGE = "This report names a failure but not what stops it recurring. Add one line: the mechanism, the tooling or process change, and its PR or owner."

ACTIVE = [CompactionState(active=True, communicator=True)]


def closing(text: str, call: str) -> list:
    return [T.user("ship it"), T.assistant(text), T.assistant(T.tool("Bash", id=call, command="ls"))]


llm_nudge(
    PROMPT,
    message=MESSAGE,
    label="self_improve_nudge",
    contexts=[AssistantMessage()],
    only_if=[Communicator()],
    skip_if=[FromSubagent()],
    once_per_turn=False,
    max_fires=None,
    transcript=False,
    agent=False,
    backend="jev",
    events=Event.PostToolUse | Event.Stop,
    tests=communicator_tests({
        Input(tool_use_id="call-report", transcript=closing("The deploy failed again: the release lane never posted its HEAD.", "call-report"), state=ACTIVE): Warn(
            pattern=r"^This report names a failure but not what stops it recurring"
        ),
        Input(
            tool_use_id="call-fixed",
            transcript=closing(
                "The deploy failed again: the release lane never posted its HEAD. The lane hook now posts HEAD on every push, PR 1690, installed in 0.7.853.",
                "call-fixed",
            ),
            state=ACTIVE,
            llm={"fire": False},
        ): Allow(),
        Input(tool_use_id="call-duplicate", transcript=closing("Duplicate; nothing new.", "call-duplicate"), state=ACTIVE, llm={"fire": False}): Allow(),
        Input(
            tool_use_id="call-ack",
            transcript=closing("Seen: the coordinator chased fix-3 on its stalled PR.\nNothing for me to do until fix-3 answers.", "call-ack"),
            state=ACTIVE,
            llm={"fire": False},
        ): Allow(),
        Input(tool_use_id="call-relay", transcript=closing("fix-3 reports CI red on lint and is fixing forward.", "call-relay"), state=ACTIVE, llm={"fire": False}): Allow(),
    }),
)
