from __future__ import annotations

import json
import re
import subprocess

from captain_hook import CONFIRMED, UNCONFIRMED, Allow, BaseHookEvent, Block, Call, Confirm, Event, FromSubagent, HookResult, Input, TaskCall, Tool, on
from captain_hook.util import reqenv

from .compaction_handoff import CompactionState
from .lane_landing import DRIVE_ENV, drive_entry, lane_name
from .owner_facing import CCI_POST, incident_sources, open_incidents, record_text
from .tests.incident_fixtures import CLOSED_0820, SAND_NOT_MIGRATED

ACTIVE = [CompactionState(active=True)]
LANE = "alane-a-0123456789abcdef"
READ_TIMEOUT_SECONDS = 20
CCN_NOT_FOUND = 3
RECORD_REF = re.compile(r"#(\d{3,})\b")
ANSWER_REF = re.compile(r"\b(?=[0-9a-f]*\d)(?=[0-9a-f]*[a-f])[0-9a-f]{7,40}\b")
UNDO_VERBS = frozenset({"cancel", "rollback"})
REVERT = re.compile(r"\b(?:revert(?:s|ed|ing)?|roll(?:s|ed|ing)?[ -]?back|rollback|back(?:ing)? out)\b", re.IGNORECASE)
ORDER_KINDS = {"go", "decision", "decide"}
PLUGIN_VERBS = frozenset({"update", "install", "uninstall", "enable", "disable"})
UNCAUSED_UNDO = (
    "A release cancel or rollback waits for a lane `MECHANISM` record naming this build as the cause, or an owner answer ordering it."
    " Find the cause first, then rerun with that `#<seq>` or answer id in the command, such as `tools/ci release cancel <run>  # MECHANISM #<seq>`."
)
UNORDERED_REVERT = (
    "A revert runs only on an owner answer that orders it; suspected regressions are fixed forward."
    " Ship the forward fix, or cite the ordering answer id in the command or brief."
)
REVERT_JUDGE = Confirm(
    rule="Block only when the text orders or performs a revert or rollback of a change with no owner answer cited that orders it;"
    " text that rules a revert out, or orders a forward fix, never blocks."
)
FROZEN_TOOLING = (
    "Incident tooling stays as it is while an incident is open."
    " Run `claude plugin update long-running` or `desk-runner.py handoff` once the incident closes."
)


def run(argv: list[str]) -> subprocess.CompletedProcess[str] | None:
    try:
        return subprocess.run(argv, capture_output=True, text=True, errors="replace", timeout=READ_TIMEOUT_SECONDS)
    except (OSError, subprocess.TimeoutExpired):
        return None


def drive_session(evt: BaseHookEvent) -> bool:
    return bool(reqenv.getenv("CLAUDE_LONG_RUNNING_LANE")) or CompactionState.load(evt).active


def mechanism(ref: str, target: str | None) -> bool | None:
    done = run(["cci", "grep", f"^#{ref} ", "--kind", "mechanism", "--json", "-n", "1"])
    if done is None or done.returncode != 0:
        return None
    lines = done.stdout.strip().splitlines()
    if not lines:
        return False
    return target is None or target in json.loads(lines[0]).get("text", "")


def owner_answer(evt: BaseHookEvent, ref: str) -> bool | None:
    if (entry := drive_entry(evt)) is None:
        return None
    done = run(["ccn", "-R", entry["checkout"], "answer", "show", ref, "--json"])
    if done is None or done.returncode not in (0, CCN_NOT_FOUND):
        return None
    return done.returncode == 0


def latest_record(evt: BaseHookEvent) -> str | None:
    done = run(["cci", "grep", ".", "--lane", lane_name(evt) or "root", "--json", "-n", "1"])
    return done.stdout if done is not None and done.returncode == 0 else None


def answer_cited(evt: BaseHookEvent, text: str) -> bool:
    return any(check is None or check for check in (owner_answer(evt, ref) for ref in dict.fromkeys(ANSWER_REF.findall(text))))


def cause_cited(evt: BaseHookEvent, text: str, target: str | None) -> bool:
    mechanisms = (mechanism(ref, target) for ref in dict.fromkeys(RECORD_REF.findall(text)))
    return any(check is None or check for check in mechanisms) or answer_cited(evt, text)


def undo_target(call: Call) -> str | None:
    return next((arg for arg in call.args[2:] if not arg.startswith("-")), None)


def undo(evt: BaseHookEvent) -> Call | None:
    return next((call for call in evt.command.calls() if call.name == "ci" and call.args[:1] == ("release",) and call.args[1:2] and call.args[1] in UNDO_VERBS), None)


def cancel(command: str, **extra: object) -> Input:
    return Input(command=command, state=ACTIVE, env=DRIVE_ENV, **extra)


@on(
    Event.PreToolUse,
    only_if=[Tool("Bash")],
    tests={
        cancel("tools/ci release cancel 2026-01-05/7", commands={"cci grep": ""}): Block(pattern=r"^A release cancel or rollback waits for a lane `MECHANISM` record"),
        cancel("./tools/ci release rollback sand --to 905", commands={"cci grep": ""}): Block(),
        cancel(
            "tools/ci release cancel 4120  # MECHANISM #500210",
            commands={"cci grep": '{"seq": 500210, "kind": "mechanism", "text": "build 4120 carries the delete; cancel it"}'},
        ): Allow(),
        cancel(
            "tools/ci release cancel 4135  # MECHANISM #500210",
            commands={"cci grep": '{"seq": 500210, "kind": "mechanism", "text": "build 4120 carries the delete; cancel it"}'},
        ): Block(),
        cancel("tools/ci release rollback sand  # owner a9e1075b", commands={"cci grep": "", "ccn": '{"id": "a9e1075b00"}'}): Allow(),
        cancel("tools/ci release rollback sand  # owner a9e1075b", commands={"cci grep": "", "ccn": (3, "not-found")}): Block(),
        cancel("tools/ci release rollback sand  # owner a9e1075b", commands={"cci grep": "", "ccn": (2, "not a git repository")}): Allow(),
        cancel("tools/ci release cancel 2026-01-05/7", commands={"cci grep": (1, "cci: daemon unavailable")}): Allow(),
        cancel(
            "tools/ci release cancel 2026-01-05/7",
            commands={"cci grep": '{"seq": 500220, "kind": "mechanism", "lane": "root", "text": "MECHANISM #500210 build 2026-01-05/7 is the cause"}'},
        ): Allow(),
        cancel("tools/ci release status 2026-01-05/7"): Allow(),
        Input(command="tools/ci release cancel 2026-01-05/7", commands={"cci grep": ""}): Allow(),
    },
)
def release_undo_cites_its_cause(evt: BaseHookEvent) -> HookResult | None:
    if (call := undo(evt)) is None or not drive_session(evt):
        return None
    target = undo_target(call)
    if cause_cited(evt, str(evt.command), target) or (latest := latest_record(evt)) is None or cause_cited(evt, latest, target):
        return None
    return evt.block(UNCAUSED_UNDO)


def revert_text(evt: BaseHookEvent) -> tuple[str, bool]:
    if (spawn := evt.as_input(TaskCall)) is not None:
        return f"{spawn.agent_name or ''}\n{spawn.raw.get('prompt') or ''}", False
    for call in evt.command.calls():
        if call.verb_argv[:2] == ("git", "revert") or call.verb_argv[:3] == ("gh", "pr", "revert"):
            return str(evt.command), True
    texts = [
        record_text(values)
        for call in evt.command.calls("cci")
        if (values := CCI_POST.bind(call).values).get("words", ())[:1] == ("post",) and ORDER_KINDS & set(values.get("kind", ()))
    ]
    return "\n".join(texts), False


def revert(command: str = "", **extra: object) -> Input:
    return Input(**({"command": command} if command else {}), state=ACTIVE, env=DRIVE_ENV, **extra)


@on(
    Event.PreToolUse,
    only_if=[Tool("Bash", "Agent")],
    skip_if=[FromSubagent()],
    tests={
        revert("git revert 5d0c9e1", commands={"ccn": (3, "not-found")}): Block(pattern=r"^A revert runs only on an owner answer that orders it"),
        revert("gh pr revert 1201 -R acme/service"): Block(),
        revert("git revert 5d0c9e1  # owner a9e1075b", commands={"ccn": '{"id": "a9e1075b00"}'}): Allow(),
        revert("git revert 5d0c9e1  # owner a9e1075b", commands={"ccn": (2, "not a git repository")}): Allow(),
        revert(tool="Agent", tool_input={"name": "inc-revert-1201", "prompt": "Revert #1201; it regressed the reader."}, decide=CONFIRMED): Block(),
        revert(tool="Agent", tool_input={"name": "inc-revert-1201", "prompt": "Revert #1201; it regressed the reader."}, decide=UNCONFIRMED): Allow(),
        revert(tool="Agent", tool_input={"name": "inc-reader-fix", "prompt": "Fix the reader forward; do not revert #1201."}, decide=UNCONFIRMED): Allow(),
        revert("cci post --drive d --lane root --kind go --to inc-fix --text 'roll back the executor to 4120'", decide=CONFIRMED): Block(),
        revert("cci post --drive d --lane root --kind head --text 'reverted the local edit'", decide=CONFIRMED): Allow(),
        revert(tool="Agent", tool_input={"name": "inc-reader-fix", "prompt": "Fix the reader forward."}, decide=CONFIRMED): Allow(),
        revert("git revert 5d0c9e1", agent_id=LANE): Allow(),
        Input(command="git revert 5d0c9e1"): Allow(),
    },
)
def reverts_need_an_owner_order(evt: BaseHookEvent) -> HookResult | None:
    text, performs = revert_text(evt)
    if not REVERT.search(text) or not CompactionState.load(evt).active or answer_cited(evt, text):
        return None
    return evt.block(UNORDERED_REVERT) if performs else evt.block(UNORDERED_REVERT, confirm=REVERT_JUDGE)


def tooling_change(evt: BaseHookEvent) -> bool:
    for call in evt.command.calls():
        if call.name == "claude" and call.args[:1] == ("plugin",) and call.args[1:2] and call.args[1] in PLUGIN_VERBS:
            if any(arg.startswith("long-running") for arg in call.args[2:]):
                return True
        if call.name == "desk-runner.py" and "handoff" in call.args:
            return True
        if call.name.startswith("python") and any(arg.endswith("desk-runner.py") for arg in call.args) and "handoff" in call.args:
            return True
    return False


def plugin(command: str, *stored: dict, **extra: object) -> Input:
    return Input(command=command, state=ACTIVE, commands=incident_sources(*stored), **extra)


@on(
    Event.PreToolUse,
    only_if=[Tool("Bash")],
    tests={
        plugin("claude plugin update long-running@skills", SAND_NOT_MIGRATED): Block(pattern=r"^Incident tooling stays as it is"),
        plugin("claude plugin install long-running@skills", SAND_NOT_MIGRATED): Block(),
        plugin("desk-runner.py handoff --desk landing --config r.json", SAND_NOT_MIGRATED): Block(),
        plugin("python3 ~/.claude/plugins/cache/skills/long-running/0.7.212/skills/long-running/scripts/desk-runner.py handoff --desk orca", SAND_NOT_MIGRATED): Block(),
        plugin("claude plugin update long-running@skills", SAND_NOT_MIGRATED, env={"CLAUDE_LONG_RUNNING_LANE": "p1-hooks"}, state=[]): Block(),
        plugin("claude plugin update long-running@skills", CLOSED_0820): Allow(),
        plugin("claude plugin update codex@skills", SAND_NOT_MIGRATED): Allow(),
        plugin("claude plugin marketplace update skills", SAND_NOT_MIGRATED): Allow(),
        Input(command="claude plugin update long-running@skills", commands=incident_sources(SAND_NOT_MIGRATED)): Allow(),
    },
)
def no_tooling_change_during_an_incident(evt: BaseHookEvent) -> HookResult | None:
    if not tooling_change(evt) or not drive_session(evt) or not open_incidents(evt):
        return None
    return evt.block(FROZEN_TOOLING)
