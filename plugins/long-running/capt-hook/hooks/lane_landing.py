from __future__ import annotations

import re

from captain_hook import Allow, BaseHookEvent, Block, Call, Event, HookResult, Input, Tool, on
from captain_hook.util import reqenv

from .compaction_handoff import CompactionState
from .owner_facing import INCIDENT_NAME
from .session_tree import nearest_name

LANDERS = re.compile(r"^(?:desk-runner|merge-train)(?:-.+)?$|^(?:\w+-)*desk(?:-\d+)?$")
TEAMMATE_NAME = re.compile(r"^a(?P<name>.+)-[0-9a-f]{16}$")
QUEUE_LABELS = frozenset({"merge", "merge-fast"})
LABEL_FIELDS = frozenset({"labels[]", "labels"})
LABELS_ENDPOINT = re.compile(r"(?:^|/)repos/[^/]+/[^/]+/issues/\d+/labels(?:\?|$)")
READ_ONLY_ENQUEUE = frozenset({"--check", "--status", "-h", "--help"})
ACTIVE = [CompactionState(active=True)]
LANE = "alane-a-0123456789abcdef"
DESK = "alanding-desk-0123456789abcdef"
ENQUEUE = "~/monorepo/.agents/skills/submit-pr/scripts/stack-enqueue 36023 --hold 30082"
REFUSAL = (
    "Lanes never enqueue: the landing desk is the only enqueuer, so lane `{lane}` may not run `{verb}`. "
    "Report the PR with `ledger.py report --drive <drive>`, and the landing desk enqueues it against the root's holds."
)


def lane_name(evt: BaseHookEvent) -> str | None:
    if evt.agent_id:
        if match := TEAMMATE_NAME.match(evt.agent_id):
            return match["name"]
        return nearest_name(evt) or reqenv.getenv("CLAUDE_LONG_RUNNING_LANE") or ""
    return reqenv.getenv("CLAUDE_LONG_RUNNING_LANE")


def label_args(args: tuple[str, ...]) -> set[str]:
    values = [
        value
        for flag, value in zip(("", *args), args, strict=False)
        if flag == "--add-label"
    ] + [arg.removeprefix("--add-label=") for arg in args if arg.startswith("--add-label=")]
    return {label.strip().casefold() for value in values for label in value.split(",")}


def labelled_fields(args: tuple[str, ...]) -> set[str]:
    return {value.casefold() for arg in args if (pair := arg.split("=", 1))[0] in LABEL_FIELDS for value in pair[1:]}


def landing_verb(call: Call) -> str | None:
    argv = call.verb_argv
    if call.name == "stack-enqueue":
        return None if READ_ONLY_ENQUEUE & set(call.args) else "stack-enqueue"
    if argv[:2] == ("gt", "merge"):
        return "gt merge"
    if argv[:3] == ("gh", "pr", "merge"):
        return "gh pr merge"
    if argv[:3] in {("gh", "pr", "edit"), ("gh", "issue", "edit")} and label_args(argv[3:]) & QUEUE_LABELS:
        return f"{' '.join(argv[:3])} --add-label"
    if argv[:2] == ("gh", "api") and any(LABELS_ENDPOINT.search(arg) for arg in argv[2:]) and labelled_fields(argv[2:]) & QUEUE_LABELS:
        return "gh api labels"
    if call.name == "ledger.py" and argv[1:2] == ("label",) and "--dry-run" not in argv:
        return "ledger.py label"
    return None


@on(
    Event.PreToolUse,
    only_if=[Tool("Bash")],
    tests={
        Input(command=ENQUEUE, agent_id=LANE, state=ACTIVE): Block(pattern=r"^Lanes never enqueue: the landing desk is the only enqueuer, so lane `lane-a` may not run `stack-enqueue`\."),
        Input(command="stack-enqueue 36039 --fast", agent_id="aL23-dashboard-wasm-follower-14fd0a86e696c1e3", state=ACTIVE): Block(pattern=r"`L23-dashboard-wasm-follower`"),
        Input(command="cd ~/wt && gt merge", agent_id=LANE, state=ACTIVE): Block(pattern=r"`gt merge`"),
        Input(command="gh pr merge 36023 --squash", agent_id=LANE, state=ACTIVE): Block(pattern=r"`gh pr merge`"),
        Input(command="gh pr edit 36023 --add-label merge", agent_id=LANE, state=ACTIVE): Block(pattern=r"`gh pr edit --add-label`"),
        Input(command="gh pr edit 36023 --add-label=docs,MERGE-FAST", agent_id=LANE, state=ACTIVE): Block(),
        Input(command="gh issue edit 36023 --add-label merge", agent_id=LANE, state=ACTIVE): Block(),
        Input(command="gh api -X POST repos/Forge-AI/monorepo/issues/36023/labels -f 'labels[]=merge'", agent_id=LANE, state=ACTIVE): Block(pattern=r"`gh api labels`"),
        Input(command="ledger.py label --ledger 1a2b --pr 36023 --expect-head abc", agent_id=LANE, state=ACTIVE): Block(pattern=r"`ledger.py label`"),
        Input(command=ENQUEUE, env={"CLAUDE_LONG_RUNNING_LANE": "lane-b"}): Block(pattern=r"`lane-b`"),
        Input(command=ENQUEUE, agent_id=DESK, state=ACTIVE): Allow(),
        Input(command=ENQUEUE, agent_id="alanding-desk-2-0123456789abcdef", state=ACTIVE): Allow(),
        Input(command=ENQUEUE, agent_id="adesk-runner-0123456789abcdef", state=ACTIVE): Allow(),
        Input(command=ENQUEUE, env={"CLAUDE_LONG_RUNNING_LANE": "landing-desk"}): Allow(),
        Input(command=ENQUEUE, agent_id="asandsql-replica-desk-0123456789abcdef", state=ACTIVE): Allow(),
        Input(command="ledger.py label --pr 36023 --expect-head abc", agent_id="amerge-train-release-0123456789abcdef", state=ACTIVE): Allow(),
        Input(command=ENQUEUE, agent_id="adesk-cleanup-lane-0123456789abcdef", state=ACTIVE): Block(),
        Input(command="stack-enqueue 36050 --fast", agent_id="aincident-api-500s-0123456789abcdef", state=ACTIVE): Allow(),
        Input(command=ENQUEUE, state=ACTIVE): Allow(),
        Input(command=ENQUEUE, agent_id=LANE): Allow(),
        Input(command="stack-enqueue 36023 --check --hold 30082", agent_id=LANE, state=ACTIVE): Allow(),
        Input(command="stack-enqueue --status 36023 36039", agent_id=LANE, state=ACTIVE): Allow(),
        Input(command="gh pr edit 36023 --add-label docs --remove-label merge", agent_id=LANE, state=ACTIVE): Allow(),
        Input(command="gh api -X DELETE repos/Forge-AI/monorepo/issues/36023/labels/merge", agent_id=LANE, state=ACTIVE): Allow(),
        Input(command="ledger.py label --dry-run --pr 36023", agent_id=LANE, state=ACTIVE): Allow(),
        Input(command="echo 'stack-enqueue 36023'", agent_id=LANE, state=ACTIVE): Allow(),
        Input(command="gh pr view 36023 --json mergedAt", agent_id=LANE, state=ACTIVE): Allow(),
    },
)
def lanes_never_enqueue(evt: BaseHookEvent) -> HookResult | None:
    if (lane := lane_name(evt)) is None or LANDERS.match(lane) or INCIDENT_NAME.match(lane):
        return None
    if evt.agent_id and not reqenv.getenv("CLAUDE_LONG_RUNNING_LANE") and not CompactionState.load(evt).active:
        return None
    verb = next((verb for call in evt.cmd.calls() if (verb := landing_verb(call))), None)
    return evt.block(REFUSAL.format(lane=lane or "unnamed", verb=verb)) if verb else None
