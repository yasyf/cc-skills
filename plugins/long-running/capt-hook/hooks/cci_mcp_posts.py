from __future__ import annotations

from cc_transcript import AttachmentEvent, DeferredToolsDelta
from captain_hook import Allow, BaseHookEvent, Event, HookResult, Input, Or, Warn, on
from captain_hook.util import reqenv

from .compaction_handoff import Bound, Communicator, Coordinator, shared_tests
from .owner_facing import CCI_POST
from .tests.root_fixtures import ACTIVE

POST_TOOL = "mcp__plugin_cc-inbox_cci__post"
CARRIED = frozenset({"words", "attr", "drive", "kind", "lane", "path", "pr", "re", "resolves", "stamp", "text", "to", "topic"})
POST = "cci post --drive d --lane root --kind report --to owner-relay --text 'merged #9'"
LISTED = [{"type": "attachment", "timestamp": "2026-11-03T09:00:00Z", "attachment": {"type": "deferred_tools_delta", "addedNames": [POST_TOOL], "addedLines": [POST_TOOL], "removedNames": []}}]
DROPPED = [*LISTED, {"type": "attachment", "timestamp": "2026-11-03T09:05:00Z", "attachment": {"type": "deferred_tools_delta", "addedNames": [], "addedLines": [], "removedNames": [POST_TOOL]}}]


class PostTheToolCarries:
    def check(self, evt: BaseHookEvent) -> bool:
        posts = [args for call in evt.command.calls("cci") if (args := CCI_POST.bind(call)).values.get("words", ())[:1] == ("post",)]
        return bool(posts) and all(not args.unread and {name for name, value in args.values.items() if value} <= CARRIED for args in posts)


class HasThePostTool:
    def check(self, evt: BaseHookEvent) -> bool:
        listed = None
        for event in evt.ctx.t.events:
            if isinstance(event, AttachmentEvent) and isinstance(found := event.detail, DeferredToolsDelta):
                if POST_TOOL in found.added_names:
                    listed = True
                elif POST_TOOL in found.removed_names:
                    listed = False
        return not reqenv.getenv("CLAUDE_LONG_RUNNING_DRIVE") if listed is None else listed


POST_THROUGH_THE_TOOL = f"Warning, nothing was blocked: This session lists the cci post tool, so post through `{POST_TOOL}`, never a Bash `cci post`. Pass the same fields as arguments; a post that needs a flag the tool lacks, such as --owned-by, still runs in Bash."


@on(
    Event.PreToolUse,
    only_if=[Bound(), Or(Coordinator(), Communicator()), PostTheToolCarries(), HasThePostTool()],
    tests=shared_tests({
        Input(command=POST, transcript=LISTED, state=ACTIVE): Warn(pattern=r"^Warning, nothing was blocked: This session lists the cci post tool, so post through `mcp__plugin_cc-inbox_cci__post`"),
        Input(command=f"cci tail --drive d --reader root --since 9 && {POST}", transcript=LISTED, state=ACTIVE): Warn(),
        Input(command="cci post --drive d --lane root --kind report --to owner-relay --path - <<'EOF'\nmerged #9\nEOF", transcript=LISTED, state=ACTIVE): Warn(),
        Input(command=POST, state=ACTIVE): Warn(pattern=r"^Warning, nothing was blocked: This session lists the cci post tool"),
        Input(command=POST, state=ACTIVE, env={"CLAUDE_LONG_RUNNING_DRIVE": "d"}): Allow(),
        Input(command=POST, transcript=DROPPED, state=ACTIVE): Allow(),
        Input(command="cci tail --drive d --reader root --since 9", transcript=LISTED, state=ACTIVE): Allow(),
        Input(command="cci post --drive d --lane root --kind decide --owned-by desk-runner --to stall-watch --text 'desk-runner owns stall-watch'", transcript=LISTED, state=ACTIVE): Allow(),
        Input(command=POST, transcript=LISTED, state=ACTIVE, agent_id="a1b2c3"): Allow(),
        Input(command=POST, transcript=LISTED): Allow(),
    }),
)
def posts_go_through_the_tool(evt: BaseHookEvent) -> HookResult:
    return evt.context(POST_THROUGH_THE_TOOL)
