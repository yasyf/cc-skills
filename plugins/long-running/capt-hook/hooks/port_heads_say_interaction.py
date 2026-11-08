from __future__ import annotations

from collections.abc import Iterator

from captain_hook import Allow, And, BaseHookEvent, Block, Event, HookResult, Input, Or, Runs, Tool, on

from .cci_mcp_posts import POST_TOOL
from .compaction_handoff import Bound
from .holds_say_what_leaves_his_board import ENV
from .owner_facing import post_values

SILENT = (
    "A port's head record names `port-of`, so it says `port-interacts=no` or `port-interacts=yes`;"
    " silent, the port waits on a tap unless fidelity happens to grade it a pure port."
    " Add `--attr port-interacts=no` when it changes nothing past its old-tree lines, else `=yes`."
)
PORT = "port-of=52626"


def head_attrs(evt: BaseHookEvent) -> Iterator[dict[str, str]]:
    if evt.tool_name == POST_TOOL:
        given = evt._tool_input
        if str(given.get("kind") or "") == "head":
            yield {str(key): str(value) for key, value in (given.get("attrs") or {}).items()}
        return
    for call in evt.command.calls("cci"):
        values = post_values(evt, call)
        if values.get("words", ())[:1] == ("post",) and ",".join(values.get("kind", ())) == "head":
            yield {key: value for key, _, value in (str(pair).partition("=") for pair in values.get("attr", ()))}


@on(
    Event.PreToolUse,
    only_if=[Bound(), Or(Tool(POST_TOOL), And(Tool("Bash"), Runs("cci", "post")))],
    tests={
        Input(command=f"cci post --kind head --pr 52628 --head 52628=a837834 --attr {PORT} --attr class=old-tree-port --text 'HEAD #52628 a837834: port-of #52626'", env=ENV): Block(pattern=r"^A port's head record names `port-of`"),
        Input(tool=POST_TOOL, tool_input={"kind": "head", "pr": ["52628"], "attrs": {"port-of": "52626"}, "text": "HEAD #52628"}, env=ENV): Block(),
        Input(command=f"cci post --kind head --pr 52628 --attr {PORT} --attr port-interacts=no --text 'HEAD #52628 a837834'", env=ENV): Allow(),
        Input(command=f"cci post --kind head --pr 52628 --attr {PORT} --attr port-interacts=yes --text 'HEAD #52628 a837834'", env=ENV): Allow(),
        Input(command="cci post --kind head --pr 52612 --text 'HEAD #52612 603f354'", env=ENV): Allow(),
        Input(command=f"cci post --kind note --pr 52628 --attr {PORT} --text 'a port of #52626'", env=ENV): Allow(),
    },
)
def port_heads_say_whether_they_interact(evt: BaseHookEvent) -> HookResult | None:
    silent = any("port-of" in attrs and attrs.get("port-interacts") not in ("no", "yes") for attrs in head_attrs(evt))
    return evt.block(SILENT) if silent else None
