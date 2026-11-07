from __future__ import annotations

from captain_hook import Allow, And, BaseHookEvent, Event, HookResult, Input, Or, Runs, Tool, Warn, on

from .cci_mcp_posts import POST_TOOL
from .compaction_handoff import Bound
from .holds_say_what_leaves_his_board import BREAKS, ENV, gates, hold_posts

INERT = "A hold gates landing only when it cites his words ordering it or names the production failure landing causes, so this one holds nothing and shows on no row he reads. Add `--attr ruling=<his answer id>` or `--attr breaks=<the failure>`, or post no hold."


@on(
    Event.PreToolUse,
    only_if=[Bound(), Or(Tool(POST_TOOL), And(Tool("Bash"), Runs("cci", "post")))],
    tests={
        Input(command="cci post --lane run-search-shape --kind hold --pr 50011 --text 'until #49305 lands'", env=ENV): Warn(pattern=r"^A hold gates landing only when.*`--attr ruling=<his answer id>` or `--attr breaks=<the failure>`"),
        Input(command="cci post --lane root --kind hold --pr 50314 --attr until=tap --text 'hold #50314: copy pass first'", env=ENV): Warn(),
        Input(tool=POST_TOOL, tool_input={"lane": "root", "kind": "hold", "pr": ["50314"], "text": "hold #50314: copy pass first"}, env=ENV): Warn(),
        Input(command=f"cci post --lane root --kind hold --pr 50314 --attr '{BREAKS}' --text 'hold #50314: the cutover has not run'", env=ENV): Allow(),
        Input(command="cci post --lane owner --kind hold --pr 50314 --text 'hold #50314: he said wait'", env=ENV): Allow(),
        Input(command="cci post --lane old-tree-port-desk-1 --kind hold --pr 46468 --attr ruling=18082c8 --text 'deletes old-tree code before Step 11'", env=ENV): Allow(),
        Input(command="cci post --lane root --kind hold --pr 50314 --resolves 4120 --text 'hold #50314 lift: the copy pass ran'", env=ENV): Allow(),
        Input(command="cci post --lane root --kind report --pr 50314 --text 'hold #50314 is in place'", env=ENV): Allow(),
    },
)
def holds_name_what_breaks(evt: BaseHookEvent) -> HookResult | None:
    return evt.context(INERT) if any(not gates(hold) for hold in hold_posts(evt)) else None
