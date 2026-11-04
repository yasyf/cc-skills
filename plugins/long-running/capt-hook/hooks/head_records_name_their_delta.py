from __future__ import annotations

from captain_hook import Allow, And, BaseHookEvent, Event, HookResult, Input, Or, Runs, Tool, Warn, on

from .cci_mcp_posts import POST_TOOL
from .compaction_handoff import Bound
from .meta_records import posts
from .pr_ledger import LANE_ENV

CLASSES = frozenset({"rebase", "review-fix", "ordered-fix", "behavior", "scope"})
NO_CLASS = "Warning, nothing was blocked: a `head` record says what its push changed, so his tap can carry without asking again. Add `--attr delta=rebase|review-fix|ordered-fix|behavior|scope`; behavior or scope returns the PR to him."


@on(
    Event.PreToolUse,
    only_if=[Bound(), Or(Tool(POST_TOOL), And(Tool("Bash"), Runs("cci", "post")))],
    tests={
        Input(command="cci post --lane x --kind head --head 12=aaaaaaa..bbbbbbb --text 'fixed the nit'", env=LANE_ENV): Warn(pattern=r"--attr delta="),
        Input(command="cci post --lane x --kind=head --head 12=bbbbbbb --attr delta=tidy", env=LANE_ENV): Warn(pattern=r"--attr delta="),
        Input(tool=POST_TOOL, tool_input={"lane": "x", "kind": "head", "text": "HEAD #12 bbbbbbb"}, env=LANE_ENV): Warn(pattern=r"--attr delta="),
        Input(command="cci post --lane x --kind head --head 12=aaaaaaa..bbbbbbb --attr delta=review-fix", env=LANE_ENV): Allow(),
        Input(tool=POST_TOOL, tool_input={"lane": "x", "kind": "head", "text": "HEAD #12 bbbbbbb", "attrs": {"delta": "rebase"}}, env=LANE_ENV): Allow(),
        Input(command="cci post --lane x --kind note --text 'a head moved'", env=LANE_ENV): Allow(),
        Input(command="cci post --lane x --kind head --head 12=bbbbbbb"): Allow(),
    },
)
def head_records_name_their_delta(evt: BaseHookEvent) -> HookResult | None:
    missing = any(kind == "head" and attrs.get("delta") not in CLASSES for _, _, kind, _, _, attrs in posts(evt))
    return evt.context(NO_CLASS) if missing else None
