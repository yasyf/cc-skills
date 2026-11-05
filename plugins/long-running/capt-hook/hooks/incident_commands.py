from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

from captain_hook import Allow, BaseHookEvent, Block, Call, Event, HookResult, Input, Tool, Warn, on
from captain_hook.util import reqenv

from .compaction_handoff import Bound, Communicator, CompactionState, judged
from .lane_landing import DRIVE_ENV, SPRITE_ENV, drive_entry, lane_name
from .owner_facing import incident_sources, open_incidents
from .tests.incident_fixtures import BARE_INCIDENT, CLOSED_0820, EARLIER_CLOSEOUT, INCIDENT_CLOSED, SAND_NOT_MIGRATED
from .tests.register_fixtures import DRIVE_CONFIG
from .tests.rulings_consumers import separate

ACTIVE = [CompactionState(active=True, coordinator=True)]
BUILD_CAUSE = '{"seq": 500210, "kind": "mechanism", "text": "build 4120 carries the delete; cancel it"}'
SAND_CAUSE = '{"seq": 500210, "kind": "mechanism", "text": "build 905 on sand carries the delete"}'
PLAN_ANSWER = '{"id": "a9e1075b00", "title": "Approve the plan?", "body": "Approved"}'
CANCEL_ANSWER = '{"id": "a9e1075b00", "title": "Cancel build 4120?", "body": "Yes, cancel 4120."}'
ROLLBACK_ANSWER = '{"id": "a9e1075b00", "title": "Roll back sand?", "body": "Yes, roll sand back to 905."}'
DECLINED_ROLLBACK = '{"id": "a9e1075b00", "title": "Roll back sand to 905?", "body": "No, fix forward."}'
READ_TIMEOUT_SECONDS = 20
AWAY_READ_SECONDS = 180
LR = Path(__file__).parents[2] / "bin" / "lr"
CCN_NOT_FOUND = 3
ANSWERS_PREFIX = "refs/cc-notes/answers/"
NOTES_FETCH_SECONDS = 60
RECORD_REF = re.compile(r"#(\d{3,})\b")
ANSWER_REF = re.compile(r"\b(?=[0-9a-f]*\d)(?=[0-9a-f]*[a-f])[0-9a-f]{7,40}\b")
REVERT = re.compile(r"\b(?:revert(?:s|ed|ing)?|roll(?:s|ed|ing)?[ -]?back|rollback|back(?:ing)? out)\b", re.IGNORECASE)
UNDO_ACTS = {"cancel": re.compile(r"\bcancel(?:s|led|ling)?\b", re.IGNORECASE), "rollback": REVERT}
DECLINED = re.compile(r"\W*(?:no|not|don'?t|do not|never|hold|reject(?:ed)?|decline[d]?)\b", re.IGNORECASE)
PLUGIN_VERBS = frozenset({"update", "install", "uninstall", "enable", "disable"})
UNCAUSED_UNDO = (
    "Warning, nothing was blocked: A release cancel or rollback needs a lane `MECHANISM` record naming this build; a rollback, or a cancel during an incident, also needs an owner answer ordering it."
    " Cite them, as in `tools/ci release rollback <stack> --to <build>  # MECHANISM #<seq> owner <answer id>`."
)
UNORDERED_ROLLBACK = (
    "A release rollback runs only on an owner answer that orders it, and a suspected regression is fixed forward."
    " Cite the answer, as in `tools/ci release rollback <stack> --to <build>  # MECHANISM #<seq> owner <answer id>`."
)
UNREAD_ON_SPRITE = (
    "Warning, nothing was blocked: the owner answer {refs} did not read on this Sprite, so this undo goes ahead unchecked."
    " Stop if he has not ordered it."
)
MISORDERED = (
    "Warning, nothing was blocked: a judge reads the owner answer this undo cites as not ordering this exact undo."
    " Read his words again before it runs, and stop if they order something else."
)
CLAUDE = "claude -p"
ORDERED = {"verdict": "no", "artifact_phrase": "", "evidence_phrase": "", "sentence": "He ordered it."}
UNORDERED = {"verdict": "yes", "artifact_phrase": "rollback sand --to 905", "evidence_phrase": "Yes", "sentence": "He said to let it finish, not to roll it back."}
TAKEOVER_NEEDS_AWAY = "A takeover runs only when `lr owner away {incident}` prints away, and a failed read counts as present. Hand it to the incident's commander with `ci oncall say`, and post this output to the coordinator.\n```\n{said}\n```"
WORD_NEEDS_THE_COMMUNICATOR = "Only the window the owner types in records his word on his presence. Send the row to owner-relay with `cci post --kind ask --to owner-relay`."
AWAY_TEXT = "away: nothing from him for 47m, and no person or commander holds inc-1"
PRESENT_TEXT = "present: he was seen 2m ago, inside 30m0s"
AWAY_CHECK = f"{LR} owner away inc-1 --drive brook-drive"
FROZEN_TOOLING = (
    "Warning, nothing was blocked: Incident tooling stays as it is while an incident is open."
    " Run `claude plugin update long-running` once the incident closes."
)


def run(argv: list[str], timeout: int = READ_TIMEOUT_SECONDS, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str] | None:
    try:
        return subprocess.run(argv, capture_output=True, text=True, errors="replace", timeout=timeout, env=env)
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


def on_sprite(evt: BaseHookEvent) -> bool:
    return (drive_entry(evt) or {}).get("host") == "sprite"


def shown_answer(evt: BaseHookEvent, ref: str) -> dict | bool | None:
    if (entry := drive_entry(evt)) is None:
        return None
    show = ["ccn", "-R", entry["checkout"], "answer", "show", ref, "--json"]
    done = run(show)
    if done is not None and done.returncode == CCN_NOT_FOUND and on_sprite(evt):
        synced = run(["ccn", "-R", entry["checkout"], "sync", "--prefix", ANSWERS_PREFIX], NOTES_FETCH_SECONDS)
        done = run(show)
        if (done is None or done.returncode == CCN_NOT_FOUND) and (synced is None or synced.returncode != 0):
            return None
    if done is None or done.returncode not in (0, CCN_NOT_FOUND):
        return None
    return False if done.returncode == CCN_NOT_FOUND else json.loads(done.stdout)


def order_text(evt: BaseHookEvent, ref: str, act: re.Pattern[str]) -> str | bool | None:
    if not isinstance(answer := shown_answer(evt, ref), dict):
        return answer
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


def cited_refs(text: str, targets: tuple[str, ...]) -> list[str]:
    return [ref for ref in dict.fromkeys(ANSWER_REF.findall(text)) if not any(target.startswith(ref) or ref.startswith(target) for target in targets)]


def order_cited(evt: BaseHookEvent, text: str, act: re.Pattern[str], targets: tuple[str, ...], every: bool = True) -> bool:
    return any(check is None or check for check in (owner_order(evt, ref, act, targets, every) for ref in cited_refs(text, targets)))


def misordered(evt: BaseHookEvent, calls: list[Call], texts: list[str]) -> HookResult | None:
    drive = (drive_entry(evt) or {}).get("drive")
    unread = []
    for call in calls:
        if not (call.args[1] == "rollback" or open_incidents(evt)):
            continue
        for ref in (ref for text in texts for ref in cited_refs(text, undo_targets(call))):
            if (answer := shown_answer(evt, ref)) is None and on_sprite(evt):
                unread.append(ref)
            if not isinstance(answer, dict):
                continue
            said = f"The owner answer the command cites, verbatim:\nQuestion: {answer.get('title') or ''}\nAnswer: {answer.get('body') or ''}"
            if found := judged("undo", said, str(evt.command), drive):
                return evt.context(f"{MISORDERED}\n{found}")
    return evt.context(UNREAD_ON_SPRITE.format(refs=", ".join(f"`{ref}`" for ref in dict.fromkeys(unread)))) if unread else None


def cause_cited(text: str, target: str | None) -> bool:
    return any(check is None or check for check in (mechanism(ref, target) for ref in dict.fromkeys(RECORD_REF.findall(text))))


def undo_targets(call: Call) -> tuple[str, ...]:
    args = call.args[2:]
    build = next((arg for arg in args if not arg.startswith("-")), None)
    to = next((value for flag, value in zip(args, args[1:]) if flag == "--to"), None) or next((arg.removeprefix("--to=") for arg in args if arg.startswith("--to=")), None)
    return tuple(target for target in (build, to) if target)


def undos(evt: BaseHookEvent) -> list[Call]:
    return [call for call in evt.command.calls() if call.name == "ci" and call.args[:1] == ("release",) and call.args[1:2] and call.args[1] in UNDO_ACTS]


def undo_ordered(evt: BaseHookEvent, call: Call, texts: list[str]) -> bool:
    targets, verb = undo_targets(call), call.args[1]
    return not (verb == "rollback" or open_incidents(evt)) or any(order_cited(evt, text, UNDO_ACTS[verb], targets) for text in texts)


def undo_settled(evt: BaseHookEvent, call: Call, texts: list[str]) -> bool:
    targets = undo_targets(call)
    return any(cause_cited(text, targets[0] if targets else None) for text in texts) and undo_ordered(evt, call, texts)


def judge_answer(verdict: dict) -> str:
    return json.dumps({"structured_output": verdict, "total_cost_usd": 0.01, "usage": {"input_tokens": 1000, "output_tokens": 50}})


def cancel(command: str, *stored: dict, **extra: object) -> Input:
    commands = incident_sources(*stored) | {CLAUDE: judge_answer(ORDERED)} | extra.pop("commands", {})
    return Input(command=command, state=ACTIVE, env=extra.pop("env", DRIVE_ENV), commands=commands, **extra)


@on(
    Event.PreToolUse,
    only_if=[Bound(), Tool("Bash")],
    tests=separate("incident_commands.release_undo_cites_its_cause", {
        cancel("tools/ci release cancel 2026-01-05/7", commands={"cci grep": ""}): Warn(pattern=r"^Warning, nothing was blocked: A release cancel or rollback needs a lane `MECHANISM` record"),
        cancel("./tools/ci release rollback sand --to 905", commands={"cci grep": ""}): Block(),
        cancel("tools/ci release cancel 4120  # MECHANISM #500210", commands={"cci grep": BUILD_CAUSE}): Allow(),
        cancel("tools/ci release cancel 4135  # MECHANISM #500210", commands={"cci grep": BUILD_CAUSE}): Warn(),
        cancel("tools/ci release cancel 41200  # MECHANISM #500210", commands={"cci grep": BUILD_CAUSE}): Warn(),
        cancel("tools/ci release cancel 4120  # MECHANISM #500210", SAND_NOT_MIGRATED, commands={"cci grep": BUILD_CAUSE}): Warn(),
        cancel("tools/ci release cancel 4120  # MECHANISM #500210 owner a9e1075b", SAND_NOT_MIGRATED, commands={"cci grep": BUILD_CAUSE, "ccn": CANCEL_ANSWER}): Allow(),
        cancel("tools/ci release cancel 4120  # MECHANISM #500210 owner a9e1075b", SAND_NOT_MIGRATED, commands={"cci grep": BUILD_CAUSE, "ccn": CANCEL_ANSWER, CLAUDE: judge_answer(UNORDERED)}): Warn(
            pattern=r"not ordering this exact undo"
        ),
        cancel("tools/ci release cancel 4120  # MECHANISM #500210 owner a9e1075b", commands={"cci grep": BUILD_CAUSE, "ccn": CANCEL_ANSWER, CLAUDE: judge_answer(UNORDERED)}): Allow(),
        cancel("tools/ci release cancel 4120  # MECHANISM #500210 owner a9e1075b", SAND_NOT_MIGRATED, commands={"cci grep": BUILD_CAUSE, "ccn": PLAN_ANSWER}): Warn(),
        cancel("tools/ci release cancel 4120  # MECHANISM #500210", CLOSED_0820, commands={"cci grep": BUILD_CAUSE}): Allow(),
        cancel("tools/ci release rollback sand --to 905  # MECHANISM #500210 owner a9e1075b", commands={"cci grep": SAND_CAUSE, "ccn": ROLLBACK_ANSWER}): Allow(),
        cancel("tools/ci release rollback sand --to 905  # MECHANISM #500210 owner a9e1075b", commands={"cci grep": SAND_CAUSE, "ccn": ROLLBACK_ANSWER, CLAUDE: judge_answer(UNORDERED)}): Warn(
            pattern=r"^Warning, nothing was blocked: a judge reads the owner answer this undo cites as not ordering this exact undo[\s\S]*let it finish"
        ),
        cancel("tools/ci release rollback sand --to 905  # MECHANISM #500210 owner a9e1075b", commands={"cci grep": SAND_CAUSE, "ccn": ROLLBACK_ANSWER, CLAUDE: (1, "model unavailable")}): Allow(),
        cancel("tools/ci release rollback sand --to 905  # MECHANISM #500210 owner a9e1075b", commands={"cci grep": SAND_CAUSE, "ccn": DECLINED_ROLLBACK, CLAUDE: judge_answer(ORDERED)}): Block(),
        cancel("tools/ci release rollback sand --to 906  # MECHANISM #500210 owner a9e1075b", commands={"cci grep": SAND_CAUSE, "ccn": ROLLBACK_ANSWER}): Block(),
        cancel("tools/ci release rollback sand --to 905  # MECHANISM #500210 owner a9e1075b", commands={"cci grep": SAND_CAUSE, "ccn": DECLINED_ROLLBACK}): Block(),
        cancel("tools/ci release rollback sand --to 905  # MECHANISM #500210 owner a9e1075b", commands={"cci grep": SAND_CAUSE, "ccn": CANCEL_ANSWER}): Block(),
        cancel("tools/ci release rollback sand --to 905  # owner a9e1075b", commands={"cci grep": "", "ccn": ROLLBACK_ANSWER}): Warn(),
        cancel("tools/ci release rollback sand --to 905  # MECHANISM #500210", commands={"cci grep": SAND_CAUSE}): Block(),
        cancel("tools/ci release rollback sand --to 905  # MECHANISM #500210 owner a9e1075b", commands={"cci grep": SAND_CAUSE, "ccn": (3, "not-found")}): Block(),
        cancel("tools/ci release rollback sand --to 905  # MECHANISM #500210 owner a9e1075b", commands={"cci grep": SAND_CAUSE, "ccn": (2, "not a git repository")}): Allow(),
        cancel("tools/ci release rollback sand --to 905  # MECHANISM #500210 owner a9e1075b", env=SPRITE_ENV, commands={"cci grep": SAND_CAUSE, "ccn": (3, "not-found"), "ccn -R /home/sprite/monorepo sync": ""}): Block(),
        cancel("tools/ci release rollback sand --to 905  # MECHANISM #500210 owner a9e1075b", env=SPRITE_ENV, commands={"cci grep": SAND_CAUSE, "ccn": (3, "not-found"), "ccn -R /home/sprite/monorepo sync": (128, "could not read from remote")}): Warn(
            pattern=r"^Warning, nothing was blocked: the owner answer `a9e1075b` did not read on this Sprite"
        ),
        cancel("tools/ci release rollback sand --to 906  # MECHANISM #500210 owner a9e1075b", env=SPRITE_ENV, commands={"cci grep": SAND_CAUSE, "ccn": ROLLBACK_ANSWER, "ccn -R /home/sprite/monorepo sync": ""}): Block(),
        cancel(
            "tools/ci release rollback web --to 77 && tools/ci release rollback sand --to 905  # MECHANISM #500210 owner a9e1075b",
            commands={"cci grep": SAND_CAUSE, "ccn": ROLLBACK_ANSWER},
        ): Block(pattern=r"^A release rollback runs only on an owner answer that orders it"),
        cancel("tools/ci release cancel 2026-01-05/7", commands={"cci grep": (1, "cci: daemon unavailable")}): Allow(),
        cancel(
            "tools/ci release cancel 2026-01-05/7",
            commands={"cci grep": '{"seq": 500220, "kind": "mechanism", "lane": "root", "text": "MECHANISM #500210 build 2026-01-05/7 is the cause"}'},
        ): Allow(),
        cancel(
            "tools/ci release rollback 2026-01-05/7",
            commands={"cci grep": '{"seq": 500220, "kind": "mechanism", "lane": "root", "text": "MECHANISM #500210 build 2026-01-05/7 is the cause"}'},
        ): Block(),
        cancel(
            "tools/ci release rollback 2026-01-05/7",
            commands={
                "cci grep": '{"seq": 500220, "kind": "go", "lane": "root", "text": "MECHANISM #500210 build 2026-01-05/7 is the cause; owner a9e1075b"}',
                "ccn": '{"id": "a9e1075b00", "title": "Roll back 2026-01-05/7?", "body": "Roll it back."}',
            },
        ): Allow(),
        cancel("tools/ci release status 2026-01-05/7"): Allow(),
        Input(command="tools/ci release cancel 2026-01-05/7", commands={"cci grep": ""}): Allow(),
    }, all_cases=True),
)
def release_undo_cites_its_cause(evt: BaseHookEvent) -> HookResult | None:
    if not (calls := undos(evt)) or not drive_session(evt):
        return None
    texts = [str(evt.command)]
    if all(undo_settled(evt, call, texts) for call in calls):
        return misordered(evt, calls, texts)
    if (latest := latest_record(evt)) is None:
        return None
    texts.append(latest)
    if any(call.args[1] == "rollback" and not undo_ordered(evt, call, texts) for call in calls):
        return evt.block(UNORDERED_ROLLBACK)
    return misordered(evt, calls, texts) if all(undo_settled(evt, call, texts) for call in calls) else evt.context(UNCAUSED_UNDO)


def tooling_change(evt: BaseHookEvent) -> bool:
    for call in evt.command.calls():
        if call.name == "claude" and call.args[:1] == ("plugin",) and call.args[1:2] and call.args[1] in PLUGIN_VERBS:
            if any(arg.startswith("long-running") for arg in call.args[2:]):
                return True
    return False


def plugin(command: str, *stored: dict, state: list[object] = ACTIVE, **extra: object) -> Input:
    return Input(command=command, state=state, commands=incident_sources(*stored), **extra)


@on(
    Event.PreToolUse,
    only_if=[Bound(), Tool("Bash")],
    tests={
        plugin("claude plugin update long-running@skills", SAND_NOT_MIGRATED): Warn(pattern=r"^Warning, nothing was blocked: Incident tooling stays as it is"),
        plugin("claude plugin install long-running@skills", SAND_NOT_MIGRATED): Warn(),
        plugin("claude plugin update long-running@skills", SAND_NOT_MIGRATED, env={"CLAUDE_LONG_RUNNING_LANE": "p1-hooks"}, state=[]): Warn(),
        plugin("claude plugin update long-running@skills", CLOSED_0820): Allow(),
        plugin("claude plugin update codex@skills", SAND_NOT_MIGRATED): Allow(),
        plugin("claude plugin marketplace update skills", SAND_NOT_MIGRATED): Allow(),
        Input(command="claude plugin update long-running@skills", commands=incident_sources(SAND_NOT_MIGRATED)): Allow(),
        Input(command="claude plugin update long-running@skills", state=ACTIVE, commands=incident_sources(posted=(BARE_INCIDENT,))): Warn(),
        Input(command="claude plugin update long-running@skills", state=ACTIVE, commands=incident_sources(posted=(BARE_INCIDENT,), closeout=(INCIDENT_CLOSED,))): Allow(),
    },
)
def no_tooling_change_during_an_incident(evt: BaseHookEvent) -> HookResult | None:
    if not tooling_change(evt) or not drive_session(evt) or not open_incidents(evt):
        return None
    return evt.context(FROZEN_TOOLING)


def takeover_incidents(evt: BaseHookEvent) -> list[str]:
    calls = (call for call in evt.command.calls() if call.name == "ci" and call.args[:2] == ("oncall", "takeover"))
    return [incident for call in calls if (incident := next((arg for arg in call.args[2:] if not arg.startswith("-")), None))]


def away_refusal(evt: BaseHookEvent, incident: str) -> HookResult | None:
    if (entry := drive_entry(evt)) is None:
        return evt.block(TAKEOVER_NEEDS_AWAY.format(incident=incident, said="present: this session names no drive"))
    done = run([str(LR), "owner", "away", incident, "--drive", entry["drive"]], timeout=AWAY_READ_SECONDS, env=dict(reqenv.env_map()))
    if done is None:
        return evt.block(TAKEOVER_NEEDS_AWAY.format(incident=incident, said=f"present: {LR} did not finish"))
    return None if done.returncode == 0 else evt.block(TAKEOVER_NEEDS_AWAY.format(incident=incident, said=(done.stdout or done.stderr).strip()))


@on(  # capt-hook: no-escape the owner ordered a real presence check before a takeover in his name, so no lane's reading or marker stands in for it
    Event.PreToolUse,
    only_if=[Bound(), Tool("Bash")],
    tests={
        Input(command="tools/ci oncall takeover inc-1", state=ACTIVE, env=DRIVE_CONFIG, commands={AWAY_CHECK: AWAY_TEXT}): Allow(),
        Input(command="tools/ci oncall takeover inc-1", state=ACTIVE, env=DRIVE_CONFIG, commands={AWAY_CHECK: (1, PRESENT_TEXT)}): Block(pattern=r"^A takeover runs only when `lr owner away inc-1` prints away[\s\S]*he was seen 2m ago"),
        Input(command="./tools/ci oncall takeover inc-1 --json  # ccx:raw", state=ACTIVE, env=DRIVE_CONFIG, commands={AWAY_CHECK: (1, PRESENT_TEXT)}): Block(),
        Input(command="tools/ci oncall takeover inc-1", state=ACTIVE, env=DRIVE_CONFIG, commands={AWAY_CHECK: (127, "lr: not found")}): Block(pattern=r"lr: not found"),
        Input(command="tools/ci oncall takeover inc-1", env={"CLAUDE_LONG_RUNNING_LANE": "dd-1-fix"}, state=[]): Block(pattern=r"names no drive"),
        Input(command="tools/ci oncall incidents --json", state=ACTIVE, env=DRIVE_CONFIG): Allow(),
        Input(command="tools/ci oncall say inc-1 --state s.json --kind finding --text takeover", state=ACTIVE, env=DRIVE_CONFIG): Allow(),
        Input(command="tools/ci oncall takeover inc-1"): Allow(),
    },
)
def takeovers_pass_lr_owner_away(evt: BaseHookEvent) -> HookResult | None:
    if not drive_session(evt):
        return None
    for incident in takeover_incidents(evt):
        if refused := away_refusal(evt, incident):
            return refused
    return None


@on(  # capt-hook: no-escape his word overrides the presence clock, so a lane recording it would be a lane's reading of his presence
    Event.PreToolUse,
    only_if=[Bound(), Tool("Bash")],
    skip_if=[Communicator()],
    tests={
        Input(command="lr owner word away --row m1", env={"CLAUDE_LONG_RUNNING_LANE": "dd-1-fix"}, state=[]): Block(pattern=r"^Only the window the owner types in"),
        Input(command="lr owner word here --row m1 --incident inc-1", state=ACTIVE): Block(),
        Input(command="lr owner word away --row m1", state=[CompactionState(active=True, communicator=True)]): Allow(),
        Input(command="lr owner away inc-1", env={"CLAUDE_LONG_RUNNING_LANE": "dd-1-fix"}, state=[]): Allow(),
    },
)
def only_the_communicator_records_his_word(evt: BaseHookEvent) -> HookResult | None:
    if any(call.name == "lr" and call.args[:2] == ("owner", "word") for call in evt.command.calls()):
        return evt.block(WORD_NEEDS_THE_COMMUNICATOR)
    return None
