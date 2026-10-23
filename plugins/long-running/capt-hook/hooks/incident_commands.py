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
BUILD_CAUSE = '{"seq": 500210, "kind": "mechanism", "text": "build 4120 carries the delete; cancel it"}'
SAND_CAUSE = '{"seq": 500210, "kind": "mechanism", "text": "build 905 on sand carries the delete"}'
PLAN_ANSWER = '{"id": "a9e1075b00", "title": "Approve the plan?", "body": "Approved"}'
CANCEL_ANSWER = '{"id": "a9e1075b00", "title": "Cancel build 4120?", "body": "Yes, cancel 4120."}'
ROLLBACK_ANSWER = '{"id": "a9e1075b00", "title": "Roll back sand?", "body": "Yes, roll sand back to 905."}'
REVERT_ANSWER = '{"id": "a9e1075b00", "title": "Revert 5d0c9e1?", "body": "Yes, revert it."}'
OTHER_REVERT_ANSWER = '{"id": "a9e1075b00", "title": "Revert 7aa0e21?", "body": "Yes, revert it."}'
PR_REVERT_ANSWER = '{"id": "a9e1075b00", "title": "Revert #1201?", "body": "Yes."}'
READ_TIMEOUT_SECONDS = 20
CCN_NOT_FOUND = 3
RECORD_REF = re.compile(r"#(\d{3,})\b")
ANSWER_REF = re.compile(r"\b(?=[0-9a-f]*\d)(?=[0-9a-f]*[a-f])[0-9a-f]{7,40}\b")
REVERT = re.compile(r"\b(?:revert(?:s|ed|ing)?|roll(?:s|ed|ing)?[ -]?back|rollback|back(?:ing)? out)\b", re.IGNORECASE)
UNDO_ACTS = {"cancel": re.compile(r"\bcancel(?:s|led|ling)?\b", re.IGNORECASE), "rollback": REVERT}
ORDER_KINDS = {"go", "decision", "decide"}
PLUGIN_VERBS = frozenset({"update", "install", "uninstall", "enable", "disable"})
UNCAUSED_UNDO = (
    "A release cancel or rollback needs a lane `MECHANISM` record naming this build and an owner answer ordering this act on it."
    " Rerun with both, such as `tools/ci release cancel <run>  # MECHANISM #<seq> owner <answer id>`."
)
UNORDERED_REVERT = (
    "A revert runs only on an owner answer that orders this revert; suspected regressions are fixed forward."
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


def names_target(text: str, target: str | None) -> bool:
    if target is None or target in text:
        return True
    return bool(ANSWER_REF.fullmatch(target)) and any(target.startswith(ref) or ref.startswith(target) for ref in ANSWER_REF.findall(text))


def owner_order(evt: BaseHookEvent, ref: str, act: re.Pattern[str], target: str | None) -> bool | None:
    if (entry := drive_entry(evt)) is None:
        return None
    done = run(["ccn", "-R", entry["checkout"], "answer", "show", ref, "--json"])
    if done is None or done.returncode not in (0, CCN_NOT_FOUND):
        return None
    if done.returncode == CCN_NOT_FOUND:
        return False
    answer = json.loads(done.stdout)
    text = f"{answer.get('title') or ''}\n{answer.get('body') or ''}"
    return bool(act.search(text)) and names_target(text, target)


def latest_record(evt: BaseHookEvent) -> str | None:
    done = run(["cci", "grep", ".", "--lane", lane_name(evt) or "root", "--json", "-n", "1"])
    return done.stdout if done is not None and done.returncode == 0 else None


def order_cited(evt: BaseHookEvent, text: str, act: re.Pattern[str], target: str | None) -> bool:
    refs = (ref for ref in dict.fromkeys(ANSWER_REF.findall(text)) if not (target and (target.startswith(ref) or ref.startswith(target))))
    return any(check is None or check for check in (owner_order(evt, ref, act, target) for ref in refs))


def cause_cited(text: str, target: str | None) -> bool:
    return any(check is None or check for check in (mechanism(ref, target) for ref in dict.fromkeys(RECORD_REF.findall(text))))


def undo_target(call: Call) -> str | None:
    return next((arg for arg in call.args[2:] if not arg.startswith("-")), None)


def undo(evt: BaseHookEvent) -> Call | None:
    return next((call for call in evt.command.calls() if call.name == "ci" and call.args[:1] == ("release",) and call.args[1:2] and call.args[1] in UNDO_ACTS), None)


def cancel(command: str, **extra: object) -> Input:
    return Input(command=command, state=ACTIVE, env=DRIVE_ENV, **extra)


@on(
    Event.PreToolUse,
    only_if=[Tool("Bash")],
    tests={
        cancel("tools/ci release cancel 2026-01-05/7", commands={"cci grep": ""}): Block(pattern=r"^A release cancel or rollback waits for a lane `MECHANISM` record"),
        cancel("./tools/ci release rollback sand --to 905", commands={"cci grep": ""}): Block(),
        cancel("tools/ci release cancel 4120  # MECHANISM #500210", commands={"cci grep": BUILD_CAUSE}): Block(),
        cancel("tools/ci release cancel 4120  # MECHANISM #500210 owner a9e1075b", commands={"cci grep": BUILD_CAUSE, "ccn": CANCEL_ANSWER}): Allow(),
        cancel("tools/ci release cancel 4120  # MECHANISM #500210 owner a9e1075b", commands={"cci grep": BUILD_CAUSE, "ccn": PLAN_ANSWER}): Block(),
        cancel("tools/ci release cancel 4135  # MECHANISM #500210 owner a9e1075b", commands={"cci grep": BUILD_CAUSE, "ccn": CANCEL_ANSWER}): Block(),
        cancel("tools/ci release rollback sand  # MECHANISM #500210 owner a9e1075b", commands={"cci grep": SAND_CAUSE, "ccn": ROLLBACK_ANSWER}): Allow(),
        cancel("tools/ci release rollback sand  # MECHANISM #500210 owner a9e1075b", commands={"cci grep": SAND_CAUSE, "ccn": CANCEL_ANSWER}): Block(),
        cancel("tools/ci release rollback sand  # owner a9e1075b", commands={"cci grep": "", "ccn": ROLLBACK_ANSWER}): Block(),
        cancel("tools/ci release rollback sand  # MECHANISM #500210 owner a9e1075b", commands={"cci grep": SAND_CAUSE, "ccn": (3, "not-found")}): Block(),
        cancel("tools/ci release rollback sand  # MECHANISM #500210 owner a9e1075b", commands={"cci grep": SAND_CAUSE, "ccn": (2, "not a git repository")}): Allow(),
        cancel("tools/ci release cancel 2026-01-05/7", commands={"cci grep": (1, "cci: daemon unavailable")}): Allow(),
        cancel(
            "tools/ci release cancel 2026-01-05/7",
            commands={"cci grep": '{"seq": 500220, "kind": "mechanism", "lane": "root", "text": "MECHANISM #500210 build 2026-01-05/7 is the cause"}'},
        ): Block(),
        cancel(
            "tools/ci release cancel 2026-01-05/7",
            commands={
                "cci grep": '{"seq": 500220, "kind": "go", "lane": "root", "text": "MECHANISM #500210 build 2026-01-05/7 is the cause; owner a9e1075b"}',
                "ccn": '{"id": "a9e1075b00", "title": "Cancel 2026-01-05/7?", "body": "Cancel it."}',
            },
        ): Allow(),
        cancel("tools/ci release status 2026-01-05/7"): Allow(),
        Input(command="tools/ci release cancel 2026-01-05/7", commands={"cci grep": ""}): Allow(),
    },
)
def release_undo_cites_its_cause(evt: BaseHookEvent) -> HookResult | None:
    if (call := undo(evt)) is None or not drive_session(evt):
        return None
    target, act, command = undo_target(call), UNDO_ACTS[call.args[1]], str(evt.command)
    caused, ordered = cause_cited(command, target), order_cited(evt, command, act, target)
    if not (caused and ordered):
        if (latest := latest_record(evt)) is None:
            return None
        caused = caused or cause_cited(latest, target)
        ordered = ordered or order_cited(evt, latest, act, target)
    return None if caused and ordered else evt.block(UNCAUSED_UNDO)


def revert_target(call: Call) -> str | None:
    operands = call.verb_argv[2:] if call.verb_argv[:1] == ("git",) else call.verb_argv[3:]
    return next((arg.lstrip("#") for arg in operands if not arg.startswith("-")), None)


def revert_text(evt: BaseHookEvent) -> tuple[str, bool, str | None]:
    if (spawn := evt.as_input(TaskCall)) is not None:
        return f"{spawn.agent_name or ''}\n{spawn.raw.get('prompt') or ''}", False, None
    for call in evt.command.calls():
        if call.verb_argv[:2] == ("git", "revert") or call.verb_argv[:3] == ("gh", "pr", "revert"):
            return str(evt.command), True, revert_target(call)
    texts = [
        record_text(values)
        for call in evt.command.calls("cci")
        if (values := CCI_POST.bind(call).values).get("words", ())[:1] == ("post",) and ORDER_KINDS & set(values.get("kind", ()))
    ]
    return "\n".join(texts), False, None


def revert(command: str = "", **extra: object) -> Input:
    return Input(**({"command": command} if command else {}), state=ACTIVE, env=DRIVE_ENV, **extra)


@on(
    Event.PreToolUse,
    only_if=[Tool("Bash", "Agent")],
    skip_if=[FromSubagent()],
    tests={
        revert("git revert 5d0c9e1", commands={"ccn": (3, "not-found")}): Block(pattern=r"^A revert runs only on an owner answer that orders it"),
        revert("gh pr revert 1201 -R acme/service"): Block(),
        revert("git revert 5d0c9e1  # owner a9e1075b", commands={"ccn": REVERT_ANSWER}): Allow(),
        revert("git revert 5d0c9e1  # owner a9e1075b", commands={"ccn": PLAN_ANSWER}): Block(),
        revert("git revert 5d0c9e1  # owner a9e1075b", commands={"ccn": OTHER_REVERT_ANSWER}): Block(),
        revert("gh pr revert 1201 -R acme/service  # owner a9e1075b", commands={"ccn": PR_REVERT_ANSWER}): Allow(),
        revert(tool="Agent", tool_input={"name": "inc-revert-1201", "prompt": "Revert #1201 per owner a9e1075b."}, commands={"ccn": PLAN_ANSWER}, decide=CONFIRMED): Block(),
        revert(tool="Agent", tool_input={"name": "inc-revert-1201", "prompt": "Revert #1201 per owner a9e1075b."}, commands={"ccn": PR_REVERT_ANSWER}, decide=CONFIRMED): Allow(),
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
    text, performs, target = revert_text(evt)
    if not REVERT.search(text) or not CompactionState.load(evt).active or order_cited(evt, text, REVERT, target):
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
