from __future__ import annotations

from captain_hook import Allow, AssistantMessage, Event, Input, T, Warn, llm_nudge

PROMPT = """Judge only the message in <assistant_message>, which the assistant just sent.
Fire when it reports something that went wrong (a failure, a defect, a miss, a refused or broken tool, a repeated owner ruling, a worker or lane that did not deliver) and does not also say what is being done so it does not recur: the mechanism, the tooling or process change, and its PR, install or owner.
Do not fire when the message reports nothing wrong, or when it names the change that stops the problem recurring and who carries it."""

MESSAGE = "This report names a failure but not what stops it recurring. Add one line: the mechanism, the tooling or process change, and its PR or owner."

REPORTED = [
    T.user("ship it"),
    T.assistant("The deploy failed again: the release lane never posted its HEAD."),
    T.assistant(T.tool("Bash", id="call-report", command="ls")),
]
FIXED = [
    T.user("ship it"),
    T.assistant(
        "The deploy failed again: the release lane never posted its HEAD. "
        "The lane hook now posts HEAD on every push, PR 1690, installed in 0.7.853."
    ),
    T.assistant(T.tool("Bash", id="call-fixed", command="ls")),
]

llm_nudge(
    PROMPT,
    message=MESSAGE,
    label="self_improve_nudge",
    contexts=[AssistantMessage()],
    once_per_turn=False,
    max_fires=None,
    transcript=False,
    agent=False,
    backend="jev",
    events=Event.PostToolUse | Event.Stop | Event.SubagentStop,
    tests={
        Input(tool_use_id="call-report", transcript=REPORTED): Warn(pattern=r"^This report names a failure but not what stops it recurring"),
        Input(tool_use_id="call-fixed", transcript=FIXED, llm={"fire": False}): Allow(),
    },
)
