from __future__ import annotations

from captain_hook import Allow, AssistantMessage, BaseHookEvent, Event, Input, T, Warn, llm_nudge

from .compaction_handoff import Communicator, communicator_tests, CompactionState

PROMPT = """Judge only the message in <assistant_message>, which the assistant just sent.
Fire only when the message itself reports, as the assistant's own news, something that went wrong (a failure, a defect, a miss, a refused or broken tool, a repeated owner ruling, a worker or lane that did not deliver) and does not also say what is being done so it does not recur: the mechanism, the tooling or process change, and its PR, install or owner.
Do not fire on an acknowledgement, a duplicate notice, a pointer to a record, or a relay or quote of what another lane, the coordinator, a teammate or the owner reported, even when the relayed words name a failure.
Do not fire when the message reports nothing wrong, or when it names the change that stops the problem recurring and who carries it."""

MESSAGE = "This report names a failure but not what stops it recurring. Add one line: the mechanism, the tooling or process change, and its PR or owner."

ACK_WORDS = 10
ACTIVE = [CompactionState(active=True)]


class LongerThanAnAck:
    def check(self, evt: BaseHookEvent) -> bool:
        return len((AssistantMessage().content(evt) or "").split()) >= ACK_WORDS


def said(text: str, call: str) -> list:
    return [T.user("ship it"), T.assistant(text), T.assistant(T.tool("Bash", id=call, command="ls"))]


llm_nudge(
    PROMPT,
    message=MESSAGE,
    label="self_improve_nudge",
    contexts=[AssistantMessage()],
    only_if=[Communicator(), LongerThanAnAck()],
    once_per_turn=False,
    max_fires=None,
    transcript=False,
    agent=False,
    backend="jev",
    events=Event.PostToolUse | Event.Stop,
    tests=communicator_tests({
        Input(
            tool_use_id="call-report",
            transcript=said("The deploy failed again: the release lane never posted its HEAD.", "call-report"),
            state=ACTIVE,
        ): Warn(pattern=r"^This report names a failure but not what stops it recurring"),
        Input(
            tool_use_id="call-fixed",
            transcript=said(
                "The deploy failed again: the release lane never posted its HEAD. "
                "The lane hook now posts HEAD on every push, PR 1690, installed in 0.7.853.",
                "call-fixed",
            ),
            state=ACTIVE,
            llm={"fire": False},
        ): Allow(),
        Input(tool_use_id="call-duplicate", transcript=said("Duplicate; nothing new.", "call-duplicate"), state=ACTIVE): Allow(),
        Input(tool_use_id="call-chase", transcript=said("Coordinator chasing its own lane; nothing for you.", "call-chase"), state=ACTIVE): Allow(),
        Input(
            tool_use_id="call-relay",
            transcript=said(
                "Mirror field is in the mirror lane's brief. One new thing on the coordinator's bus: several PR builds, "
                "including the Fable lane's first two release PRs, are failing within 30 seconds of starting, all at once, "
                "cause not yet read. If it is a CI-wide break it slows the stuck-PR pass; I tell you the cause when it lands.",
                "call-relay",
            ),
            state=ACTIVE,
            llm={"fire": False},
        ): Allow(),
        Input(
            tool_use_id="call-quote",
            transcript=said(
                "Josh's report is a 500 from our API on the run's object-store credentials, breaking old-app runs all day. "
                "Spawning the incident fix and evidence lanes now and reading the other two threads.",
                "call-quote",
            ),
            state=ACTIVE,
            llm={"fire": False},
        ): Allow(),
    }),
)
