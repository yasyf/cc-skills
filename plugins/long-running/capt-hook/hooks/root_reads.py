from __future__ import annotations

from captain_hook import Allow, BaseHookEvent, Input, Or, RanCommand, Runs, T, Tool, UsedTool, Warn

from .root_context import root_warning
from .tests.root_fixtures import ACTIVE

READ_TOOLS = "Read|Grep|WebFetch|mcp__plugin_cc-slack_cc-slack__slack_thread|mcp__slack__slack_get_thread"
READ_COMMANDS = (("cci", "tail"), ("cci", "show"), ("cci", "grep"), ("gh", "pr", "view"), ("gh", "pr", "checks"), ("ccx", "vcs", "reviews"), ("cat",), ("head",), ("sed",))
LIVENESS_FILTERS = frozenset({"tail", "head", "grep", "rg"})
STATUS = "/drive/progress/owner/update-1733.md"


class TailsALane:
    def check(self, evt: BaseHookEvent) -> bool:
        calls = evt.command.calls()
        tails = any(call.name == "cci" and call.args[:1] == ("tail",) and any(arg == "--lane" or arg.startswith("--lane=") for arg in call.args) for call in calls)
        return tails and any(call.name in LIVENESS_FILTERS for call in calls)


def earlier(*calls) -> list:
    return [T.user("where are we on everything"), T.assistant(*calls)]


root_warning(
    message="Warning, nothing was blocked: The drive root reads one source per owner ask. Relay the progress desk's newest update path unchanged, and hand any further read to a lane with `Agent`.",
    only_if=[Or(Tool(READ_TOOLS), *(Runs(*argv) for argv in READ_COMMANDS)), Or(UsedTool(READ_TOOLS, subagents=False), *(RanCommand(*argv) for argv in READ_COMMANDS))],
    tests={
        Input(tool="Read", tool_input={"file_path": STATUS}, state=ACTIVE, transcript=earlier(T.tool("Bash", command="cci tail --drive d --since 342000"))): Warn(pattern=r"one source per owner ask"),
        Input(command="gh pr view 48401", state=ACTIVE, transcript=earlier(T.tool("Read", file_path=STATUS))): Warn(),
        Input(command="cci grep --drive d d456d35", state=ACTIVE, transcript=earlier(T.tool("Read", file_path=STATUS))): Warn(),
        Input(tool="Read", tool_input={"file_path": STATUS}, state=ACTIVE, transcript=earlier()): Allow(),
        Input(command="gh pr view 48401", state=ACTIVE, transcript=earlier(T.tool("Agent", name="pr-reader", prompt="Read PR 48401."))): Allow(),
        Input(command="gh pr view 48401", transcript=earlier(T.tool("Read", file_path=STATUS))): Allow(),
        Input(command="gh pr view 48401", agent_id="a1b2c3", state=ACTIVE, transcript=earlier(T.tool("Read", file_path=STATUS))): Allow(),
    },
)

root_warning(
    message="Warning, nothing was blocked: A lane's liveness and newest record come from the registry, never a tail. Run `drive_supervisor.py lanes <lane> --config <lr.json>`.",
    only_if=[TailsALane()],
    tests={
        Input(command="cci tail --drive d --lane gateway-gemini-1 --since 340000 | tail -n 3", state=ACTIVE): Warn(pattern=r"drive_supervisor\.py lanes"),
        Input(command="cci tail --drive d --lane=work-system-build-1 --budget 2000 | grep -c STATE", state=ACTIVE): Warn(),
        Input(command="cci tail --drive d --lane gateway-gemini-1 --since 340000", state=ACTIVE): Allow(),
        Input(command="cci tail --drive d --since 340000 | grep MECHANISM", state=ACTIVE): Allow(),
        Input(command="cci tail --drive d --lane gateway-gemini-1 | tail -n 3"): Allow(),
    },
)
