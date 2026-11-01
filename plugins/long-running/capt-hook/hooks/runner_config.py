from __future__ import annotations

from captain_hook import Allow, BaseHookEvent, Event, HookResult, Input, Tool, Warn, on

RUNNER_CONFIG = "runner.json"
CONFIG_PATH = "/home/u/.claude/scratch/d1/runner.json"
THROUGH_THE_VERB = (
    "Warning, nothing was blocked: a hand edit of runner.json can stop the desk at its next start."
    " Use `desk-runner.py config set <key> '<json>' --config <runner.json>` or `config unset <key>`, which writes only when the desk would start on it."
)


@on(
    Event.PreToolUse,
    only_if=[Tool("Edit", "Write")],
    tests={
        Input(tool="Edit", tool_input={"file_path": CONFIG_PATH, "old_string": '"fidelity-desk"', "new_string": '"fidelity-desk", "fidelity-desk-14"'}): Warn(pattern=r"desk-runner\.py config set"),
        Input(tool="Write", tool_input={"file_path": CONFIG_PATH, "content": "{}"}): Warn(),
        Input(tool="Write", tool_input={"file_path": "/home/u/.claude/scratch/d1/runner.json.bak", "content": "{}"}): Allow(),
        Input(tool="Edit", tool_input={"file_path": "/repo/package.json", "old_string": "a", "new_string": "b"}): Allow(),
    },
)
def runner_config_through_the_verb(evt: BaseHookEvent) -> HookResult | None:
    return evt.warn(THROUGH_THE_VERB) if evt.file.path.name == RUNNER_CONFIG else None
