from __future__ import annotations

import json
import re
import subprocess

from captain_hook import Allow, BaseHookEvent, Call, Event, FromSubagent, HookResult, Input, TaskCall, Tool, Warn, on
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
DECLINED_ROLLBACK = '{"id": "a9e1075b00", "title": "Roll back sand to 905?", "body": "No, fix forward."}'
REVERT_ANSWER = '{"id": "a9e1075b00", "title": "Revert 5d0c9e1?", "body": "Yes, revert it."}'
OTHER_REVERT_ANSWER = '{"id": "a9e1075b00", "title": "Revert 7aa0e21?", "body": "Yes, revert it."}'
DECLINED_REVERT = '{"id": "a9e1075b00", "title": "Revert #1201?", "body": "No, fix forward."}'
SUPERSEDED_REVERT = '{"id": "a9e1075b00", "title": "Revert 5d0c9e1?", "body": "Yes, revert it.", "superseded_by": ["b7c0d2e900"]}'
PR_REVERT_ANSWER = '{"id": "a9e1075b00", "title": "Revert #1201?", "body": "Yes."}'
READ_TIMEOUT_SECONDS = 20
CCN_NOT_FOUND = 3
RECORD_REF = re.compile(r"#(\d{3,})\b")
ANSWER_REF = re.compile(r"\b(?=[0-9a-f]*\d)(?=[0-9a-f]*[a-f])[0-9a-f]{7,40}\b")
REVERT = re.compile(r"\b(?:revert(?:s|ed|ing)?|roll(?:s|ed|ing)?[ -]?back|rollback|back(?:ing)? out)\b", re.IGNORECASE)
UNDO_ACTS = {"cancel": re.compile(r"\bcancel(?:s|led|ling)?\b", re.IGNORECASE), "rollback": REVERT}
DECLINED = re.compile(r"\W*(?:no|not|don'?t|do not|never|hold|reject(?:ed)?|decline[d]?)\b", re.IGNORECASE)
PR_REF = re.compile(r"#(\d+)\b")
ORDER_KINDS = {"go", "decision", "decide"}
PLUGIN_VERBS = frozenset({"update", "install", "uninstall", "enable", "disable"})
UNCAUSED_UNDO = (
    "Warning, nothing was blocked: A release cancel or rollback needs a lane `MECHANISM` record naming this build; a rollback, or a cancel during an open incident, also needs an owner answer ordering it."
    " Rerun citing them, such as `tools/ci release rollback <stack> --to <build>  # MECHANISM #<seq> owner <answer id>`."
)
UNORDERED_REVERT = (
    "Warning, nothing was blocked: A revert runs only on an owner answer that orders this revert; suspected regressions are fixed forward."
    " Ship the forward fix, or cite the ordering answer id in the command or brief."
)
FROZEN_TOOLING = (
    "Warning, nothing was blocked: Incident tooling stays as it is while an incident is open."
    " Run `claude plugin update long-running` or `desk-runner.py handoff` once the incident closes."
)


def run(argv: list[str]) -> subprocess.CompletedProcess[str] | None:
    try:
        return subprocess.run(argv, capture_output=True, text=True, errors="replace", timeout=READ_TIMEOUT_SECONDS)
    except (OSError, subprocess.TimeoutExpired):
        return None


def drive_session(evt: BaseHookEvent) -> bool:
    return bool(reqenv.getenv("CLAUDE_LONG_RUNNING_LANE")) or CompactionState.load(evt).active


def token(target: str) -> re.Pattern[str]:
    return re.compile(rf"(?<![\w-]){re.escape(target)}(?![\w-])")


def mechanism(ref: str, target: str | None) -> bool | None:
    done = run(["cci", "grep", f"^#{ref} ", "--kind", "mechanism", "--json", "-n", "1"])
    if done is None or done.returncode != 0:
        return None
    lines = done.stdout.strip().splitlines()
    if not lines:
        return False
    return target is None or bool(token(target).search(json.loads(lines[0]).get("text", "")))


def names_target(text: str, target: str) -> bool:
    if token(target).search(text):
        return True
    return bool(ANSWER_REF.fullmatch(target)) and any(target.startswith(ref) or ref.startswith(target) for ref in ANSWER_REF.findall(text))


def order_text(evt: BaseHookEvent, ref: str, act: re.Pattern[str]) -> str | bool | None:
    if (entry := drive_entry(evt)) is None:
        return None
    done = run(["ccn", "-R", entry["checkout"], "answer", "show", ref, "--json"])
    if done is None or done.returncode not in (0, CCN_NOT_FOUND):
        return None
    if done.returncode == CCN_NOT_FOUND:
        return False
    answer = json.loads(done.stdout)
    body = answer.get("body") or ""
    text = f"{answer.get('title') or ''}\n{body}"
    return text if act.search(text) and not answer.get("superseded_by") and not DECLINED.match(body) else True


def owner_order(evt: BaseHookEvent, ref: str, act: re.Pattern[str], targets: tuple[str, ...], every: bool) -> bool | None:
    if (text := order_text(evt, ref, act)) is None:
        return None
    if not isinstance(text, str):
        return False
    return (all if every else any)(names_target(text, target) for target in targets) if targets else True


def latest_record(evt: BaseHookEvent) -> str | None:
    done = run(["cci", "grep", ".", "--lane", lane_name(evt) or "root", "--json", "-n", "1"])
    return done.stdout if done is not None and done.returncode == 0 else None


def order_cited(evt: BaseHookEvent, text: str, act: re.Pattern[str], targets: tuple[str, ...], every: bool = True) -> bool:
    refs = (ref for ref in dict.fromkeys(ANSWER_REF.findall(text)) if not any(target.startswith(ref) or ref.startswith(target) for target in targets))
    return any(check is None or check for check in (owner_order(evt, ref, act, targets, every) for ref in refs))


def text_order_cited(evt: BaseHookEvent, text: str) -> bool:
    hexes = dict.fromkeys(ANSWER_REF.findall(text))
    found = {ref: order_text(evt, ref, REVERT) for ref in hexes}
    if None in found.values():
        return True
    orders = [order for order in found.values() if isinstance(order, str)]
    targets = [*dict.fromkeys(PR_REF.findall(text)), *(ref for ref, order in found.items() if order is False)]
    return bool(orders) and all(any(names_target(order, target) for order in orders) for target in targets)


def cause_cited(text: str, target: str | None) -> bool:
    return any(check is None or check for check in (mechanism(ref, target) for ref in dict.fromkeys(RECORD_REF.findall(text))))


def undo_targets(call: Call) -> tuple[str, ...]:
    args = call.args[2:]
    build = next((arg for arg in args if not arg.startswith("-")), None)
    to = next((value for flag, value in zip(args, args[1:]) if flag == "--to"), None) or next((arg.removeprefix("--to=") for arg in args if arg.startswith("--to=")), None)
    return tuple(target for target in (build, to) if target)


def undos(evt: BaseHookEvent) -> list[Call]:
    return [call for call in evt.command.calls() if call.name == "ci" and call.args[:1] == ("release",) and call.args[1:2] and call.args[1] in UNDO_ACTS]


def undo_settled(evt: BaseHookEvent, call: Call, texts: list[str]) -> bool:
    targets, verb = undo_targets(call), call.args[1]
    build = targets[0] if targets else None
    needs_order = verb == "rollback" or bool(open_incidents(evt))
    return any(cause_cited(text, build) for text in texts) and (not needs_order or any(order_cited(evt, text, UNDO_ACTS[verb], targets) for text in texts))


def cancel(command: str, *stored: dict, **extra: object) -> Input:
    commands = incident_sources(*stored) | extra.pop("commands", {})
    return Input(command=command, state=ACTIVE, env=DRIVE_ENV, commands=commands, **extra)


@on(
    Event.PreToolUse,
    only_if=[Tool("Bash")],
    tests={
        cancel("tools/ci release cancel 2026-01-05/7", commands={"cci grep": ""}): Warn(pattern=r"^Warning, nothing was blocked: A release cancel or rollback needs a lane `MECHANISM` record"),
        cancel("./tools/ci release rollback sand --to 905", commands={"cci grep": ""}): Warn(),
        cancel("tools/ci release cancel 4120  # MECHANISM #500210", commands={"cci grep": BUILD_CAUSE}): Allow(),
        cancel("tools/ci release cancel 4135  # MECHANISM #500210", commands={"cci grep": BUILD_CAUSE}): Warn(),
        cancel("tools/ci release cancel 41200  # MECHANISM #500210", commands={"cci grep": BUILD_CAUSE}): Warn(),
        cancel("tools/ci release cancel 4120  # MECHANISM #500210", SAND_NOT_MIGRATED, commands={"cci grep": BUILD_CAUSE}): Warn(),
        cancel("tools/ci release cancel 4120  # MECHANISM #500210 owner a9e1075b", SAND_NOT_MIGRATED, commands={"cci grep": BUILD_CAUSE, "ccn": CANCEL_ANSWER}): Allow(),
        cancel("tools/ci release cancel 4120  # MECHANISM #500210 owner a9e1075b", SAND_NOT_MIGRATED, commands={"cci grep": BUILD_CAUSE, "ccn": PLAN_ANSWER}): Warn(),
        cancel("tools/ci release cancel 4120  # MECHANISM #500210", CLOSED_0820, commands={"cci grep": BUILD_CAUSE}): Allow(),
        cancel("tools/ci release rollback sand --to 905  # MECHANISM #500210 owner a9e1075b", commands={"cci grep": SAND_CAUSE, "ccn": ROLLBACK_ANSWER}): Allow(),
        cancel("tools/ci release rollback sand --to 906  # MECHANISM #500210 owner a9e1075b", commands={"cci grep": SAND_CAUSE, "ccn": ROLLBACK_ANSWER}): Warn(),
        cancel("tools/ci release rollback sand --to 905  # MECHANISM #500210 owner a9e1075b", commands={"cci grep": SAND_CAUSE, "ccn": DECLINED_ROLLBACK}): Warn(),
        cancel("tools/ci release rollback sand --to 905  # MECHANISM #500210 owner a9e1075b", commands={"cci grep": SAND_CAUSE, "ccn": CANCEL_ANSWER}): Warn(),
        cancel("tools/ci release rollback sand --to 905  # owner a9e1075b", commands={"cci grep": "", "ccn": ROLLBACK_ANSWER}): Warn(),
        cancel("tools/ci release rollback sand --to 905  # MECHANISM #500210", commands={"cci grep": SAND_CAUSE}): Warn(),
        cancel("tools/ci release rollback sand --to 905  # MECHANISM #500210 owner a9e1075b", commands={"cci grep": SAND_CAUSE, "ccn": (3, "not-found")}): Warn(),
        cancel("tools/ci release rollback sand --to 905  # MECHANISM #500210 owner a9e1075b", commands={"cci grep": SAND_CAUSE, "ccn": (2, "not a git repository")}): Allow(),
        cancel(
            "tools/ci release rollback web --to 77 && tools/ci release rollback sand --to 905  # MECHANISM #500210 owner a9e1075b",
            commands={"cci grep": SAND_CAUSE, "ccn": ROLLBACK_ANSWER},
        ): Warn(),
        cancel("tools/ci release cancel 2026-01-05/7", commands={"cci grep": (1, "cci: daemon unavailable")}): Allow(),
        cancel(
            "tools/ci release cancel 2026-01-05/7",
            commands={"cci grep": '{"seq": 500220, "kind": "mechanism", "lane": "root", "text": "MECHANISM #500210 build 2026-01-05/7 is the cause"}'},
        ): Allow(),
        cancel(
            "tools/ci release rollback 2026-01-05/7",
            commands={"cci grep": '{"seq": 500220, "kind": "mechanism", "lane": "root", "text": "MECHANISM #500210 build 2026-01-05/7 is the cause"}'},
        ): Warn(),
        cancel(
            "tools/ci release rollback 2026-01-05/7",
            commands={
                "cci grep": '{"seq": 500220, "kind": "go", "lane": "root", "text": "MECHANISM #500210 build 2026-01-05/7 is the cause; owner a9e1075b"}',
                "ccn": '{"id": "a9e1075b00", "title": "Roll back 2026-01-05/7?", "body": "Roll it back."}',
            },
        ): Allow(),
        cancel("tools/ci release status 2026-01-05/7"): Allow(),
        Input(command="tools/ci release cancel 2026-01-05/7", commands={"cci grep": ""}): Allow(),
    },
)
def release_undo_cites_its_cause(evt: BaseHookEvent) -> HookResult | None:
    if not (calls := undos(evt)) or not drive_session(evt):
        return None
    texts = [str(evt.command)]
    if all(undo_settled(evt, call, texts) for call in calls):
        return None
    if (latest := latest_record(evt)) is None:
        return None
    texts.append(latest)
    return None if all(undo_settled(evt, call, texts) for call in calls) else evt.context(UNCAUSED_UNDO)


def revert_target(call: Call) -> str | None:
    operands = call.verb_argv[2:] if call.verb_argv[:1] == ("git",) else call.verb_argv[3:]
    return next((arg.lstrip("#") for arg in operands if not arg.startswith("-")), None)


def reverts(evt: BaseHookEvent) -> list[Call]:
    return [call for call in evt.command.calls() if call.verb_argv[:2] == ("git", "revert") or call.verb_argv[:3] == ("gh", "pr", "revert")]


def ordered_text(evt: BaseHookEvent) -> str:
    if (spawn := evt.as_input(TaskCall)) is not None:
        return f"{spawn.agent_name or ''}\n{spawn.raw.get('prompt') or ''}"
    texts = [
        record_text(values)
        for call in evt.command.calls("cci")
        if (values := CCI_POST.bind(call).values).get("words", ())[:1] == ("post",) and ORDER_KINDS & set(values.get("kind", ()))
    ]
    return "\n".join(texts)


def revert(command: str = "", **extra: object) -> Input:
    return Input(**({"command": command} if command else {}), state=ACTIVE, env=DRIVE_ENV, **extra)


@on(
    Event.PreToolUse,
    only_if=[Tool("Bash")],
    skip_if=[FromSubagent()],
    tests={
        revert("git revert 5d0c9e1", commands={"ccn": (3, "not-found")}): Warn(pattern=r"^Warning, nothing was blocked: A revert runs only on an owner answer that orders this revert"),
        revert("git revert 7aa0e21 && git revert 5d0c9e1  # owner a9e1075b", commands={"ccn": REVERT_ANSWER}): Warn(),
        revert("gh pr revert 1201 -R acme/service  # owner a9e1075b", commands={"ccn": DECLINED_REVERT}): Warn(),
        revert("gh pr revert 1201 -R acme/service"): Warn(),
        revert("git revert 5d0c9e1  # owner a9e1075b", commands={"ccn": REVERT_ANSWER}): Allow(),
        revert("git revert 5d0c9e1  # owner a9e1075b", commands={"ccn": PLAN_ANSWER}): Warn(),
        revert("git revert 5d0c9e1  # owner a9e1075b", commands={"ccn": SUPERSEDED_REVERT}): Warn(),
        revert("git revert 5d0c9e1  # owner a9e1075b", commands={"ccn": OTHER_REVERT_ANSWER}): Warn(),
        revert("gh pr revert 1201 -R acme/service  # owner a9e1075b", commands={"ccn": PR_REVERT_ANSWER}): Allow(),
        revert("git revert 5d0c9e1  # owner a9e1075b", commands={"ccn": (2, "not a git repository")}): Allow(),
        revert("git revert 5d0c9e1", agent_id=LANE): Allow(),
        Input(command="git revert 5d0c9e1"): Allow(),
    },
)
def reverts_need_an_owner_order(evt: BaseHookEvent) -> HookResult | None:
    if not (calls := reverts(evt)) or not CompactionState.load(evt).active:
        return None
    command = str(evt.command)
    unordered = [call for call in calls if not order_cited(evt, command, REVERT, tuple(filter(None, [revert_target(call)])))]
    return evt.context(UNORDERED_REVERT) if unordered else None


@on(
    Event.PreToolUse,
    only_if=[Tool("Bash", "Agent")],
    skip_if=[FromSubagent()],
    tests={
        revert(tool="Agent", tool_input={"name": "inc-revert-1202", "prompt": "Revert #1202 per owner a9e1075b."}, commands={"ccn": PR_REVERT_ANSWER}): Warn(),
        revert(tool="Agent", tool_input={"name": "inc-revert", "prompt": "Revert 7aa0e21 per owner a9e1075b."}, commands={"ccn": OTHER_REVERT_ANSWER}): Allow(),
        revert(tool="Agent", tool_input={"name": "inc-revert", "prompt": "Revert #1201 and #1202 per owner a9e1075b."}, commands={"ccn": PR_REVERT_ANSWER}): Warn(),
        revert(tool="Agent", tool_input={"name": "inc-revert-1201", "prompt": "Revert #1201 per owner a9e1075b."}, commands={"ccn": PLAN_ANSWER}): Warn(),
        revert(tool="Agent", tool_input={"name": "inc-revert-1201", "prompt": "Revert #1201 per owner a9e1075b."}, commands={"ccn": PR_REVERT_ANSWER}): Allow(),
        revert(tool="Agent", tool_input={"name": "inc-revert-1201", "prompt": "Revert #1201; it regressed the reader."}): Warn(),
        revert(tool="Agent", tool_input={"name": "inc-reader-fix", "prompt": "Fix the reader forward; do not revert #1201."}): Warn(),
        revert("cci post --drive d --lane root --kind go --to inc-fix --text 'roll back the executor to 4120'"): Warn(),
        revert("cci post --drive d --lane root --kind head --text 'reverted the local edit'"): Allow(),
        revert(tool="Agent", tool_input={"name": "inc-reader-fix", "prompt": "Fix the reader forward."}): Allow(),
        revert("git revert 5d0c9e1", commands={"ccn": (3, "not-found")}): Allow(),
    },
)
def revert_orders_need_an_owner_order(evt: BaseHookEvent) -> HookResult | None:
    text = ordered_text(evt)
    if not REVERT.search(text) or not CompactionState.load(evt).active or text_order_cited(evt, text):
        return None
    return evt.context(UNORDERED_REVERT)


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


def plugin(command: str, *stored: dict, state: list[object] = ACTIVE, **extra: object) -> Input:
    return Input(command=command, state=state, commands=incident_sources(*stored), **extra)


@on(
    Event.PreToolUse,
    only_if=[Tool("Bash")],
    tests={
        plugin("claude plugin update long-running@skills", SAND_NOT_MIGRATED): Warn(pattern=r"^Warning, nothing was blocked: Incident tooling stays as it is"),
        plugin("claude plugin install long-running@skills", SAND_NOT_MIGRATED): Warn(),
        plugin("desk-runner.py handoff --desk landing --config r.json", SAND_NOT_MIGRATED): Warn(),
        plugin("python3 ~/.claude/plugins/cache/skills/long-running/0.7.212/skills/long-running/scripts/desk-runner.py handoff --desk orca", SAND_NOT_MIGRATED): Warn(),
        plugin("claude plugin update long-running@skills", SAND_NOT_MIGRATED, env={"CLAUDE_LONG_RUNNING_LANE": "p1-hooks"}, state=[]): Warn(),
        plugin("claude plugin update long-running@skills", CLOSED_0820): Allow(),
        plugin("claude plugin update codex@skills", SAND_NOT_MIGRATED): Allow(),
        plugin("claude plugin marketplace update skills", SAND_NOT_MIGRATED): Allow(),
        Input(command="claude plugin update long-running@skills", commands=incident_sources(SAND_NOT_MIGRATED)): Allow(),
    },
)
def no_tooling_change_during_an_incident(evt: BaseHookEvent) -> HookResult | None:
    if not tooling_change(evt) or not drive_session(evt) or not open_incidents(evt):
        return None
    return evt.context(FROZEN_TOOLING)
