from __future__ import annotations

import json
import re
import subprocess

from captain_hook import Allow, BaseHookEvent, Block, Call, Event, HookResult, Input, Tool, Warn, on
from captain_hook.util import reqenv

from .call_dir import UnknownDirectory, run_dir
from .compaction_handoff import Bound, bus_name, drive_entry
from .gate5_release import LR
from .incident_commands import drive_session
from .lane_landing import DRIVE_ENV
from .owner_facing import INCIDENT_DIGEST, incident_sources, open_incidents, read_incident_source
from .tests.incident_fixtures import SAND_NOT_MIGRATED

RELEASE_VERBS = frozenset({"start", "hotfix"})
PULL_WORD = re.compile(r"^(#?\d+,?)+$")
WORD = re.compile(r"[a-z0-9]+")
PLAN_SECONDS = 120
BOARD_SECONDS = 30
GATED = "{stack} under card `{card}`"
REFUSAL = "This release ships what an open owner card gates: {gated}. Start it again with `--without {stacks}`, or wait for his tap on the card and start it once he answers."
WHY_CHARS = 80
UNREAD = "Warning, nothing was blocked: release_cards could not read {what} ({why}). Check `tools/ci release plan` and `lr board waits` yourself before the start applies."

LANE_ENV = DRIVE_ENV | {"CLAUDE_LONG_RUNNING_LANE": "release-6"}
SANDSQL_CARD = {"kind": "pick", "block": "card-sandsql-release-without", "subject": "sandsql-choice", "title": "Release SandSQL with the next cut, or leave it out until the replica fix lands?"}
OTHER_CARD = {"kind": "pick", "block": "card-r1-scenarios", "subject": "r1-choice", "title": "Run release 1's scenarios tonight?"}
TITLED_CARD = {"kind": "pick", "block": "card-index-order", "subject": "order-choice", "title": "Ship SandSQL to plat before the index release?"}
BOARD = f"{LR} board waits --drive release-v3 --json"
PLAN_12451 = "tools/ci release plan --targets db,restate,code-sandbox --json"


def pulls(names: tuple[str, ...]) -> list[dict]:
    return [{"component": name, "env": "plat-usw2-prod", "why": {"kind": "reads"}} for name in names]


def planned(*additions: str, picked: tuple[str, ...] = ("db", "restate", "code-sandbox"), left_out: tuple[str, ...] = ()) -> str:
    plan = {"picked": [{"component": name, "parts": []} for name in picked], "additions": pulls(additions), "leftOut": pulls(left_out), "holds": [], "frozen": []}
    return json.dumps({"commit": "45b094c", "plan": plan, "text": []})


def release(command: str, plan: str | tuple[int, str] | None = None, cards: tuple[dict, ...] = (SANDSQL_CARD,), stored: tuple[dict, ...] = (), env: dict[str, str] = LANE_ENV, **commands: str | tuple[int, str]) -> Input:
    board = json.dumps([card | {"line": f"pick {card['block']}"} for card in cards])
    return Input(command=command, env=env, commands=incident_sources(*stored) | {BOARD: board} | ({PLAN_12451: plan} if plan is not None else {}) | commands)


class Unread(Exception):
    pass


def read(argv: list[str], seconds: int, cwd: str | None = None) -> str:
    try:
        done = subprocess.run(argv, capture_output=True, text=True, timeout=seconds, cwd=cwd, env=dict(reqenv.env_map()))
    except (OSError, subprocess.TimeoutExpired) as failed:
        raise Unread(f"`{' '.join(argv[1:3])}`: {failed}") from failed
    if done.returncode:
        raise Unread(f"`{' '.join(argv[1:3])}` exited {done.returncode}: {(done.stderr or done.stdout).strip()}")
    return done.stdout


def plan_words(call: Call) -> list[str]:
    words = [word for word in call.args[2:] if word != "--json"]
    if call.args[1] == "start":
        return words
    return ["--hotfix", ",".join(word.strip("#,") for word in words if PULL_WORD.match(word)), *(word for word in words if not PULL_WORD.match(word))]


def shipped(call: Call, cwd: str) -> set[str]:
    plan = json.loads(read([call.command.executable, "release", "plan", *plan_words(call), "--json"], PLAN_SECONDS, cwd))["plan"]
    return {name for picked in plan.get("picked") or () for name in (picked["component"], *(picked.get("parts") or ()))} | {pull["component"] for pull in plan.get("additions") or ()}


def unread(evt: BaseHookEvent, what: str, why: object) -> HookResult:
    return evt.context(UNREAD.format(what=what, why=str(why)[:WHY_CHARS]))


def words(text: str) -> tuple[str, ...]:
    return tuple(WORD.findall(text.lower()))


def names(text: str, stack: str) -> bool:
    said, wanted = words(text), words(stack)
    return any(said[start : start + len(wanted)] == wanted for start in range(len(said) - len(wanted) + 1))


def gating(stacks: set[str], cards: list[dict]) -> list[tuple[str, dict]]:
    return [(stack, card) for card in cards for stack in sorted(stacks) if names(card["block"], stack) or names(card.get("title") or "", stack)]


@on(
    Event.PreToolUse,
    only_if=[Bound(), Tool("Bash")],
    tests={
        release("tools/ci release start --targets db,restate,code-sandbox", planned("sandsql")): Block(
            pattern=r"^This release ships what an open owner card gates: sandsql under card `card-sandsql-release-without`[\s\S]*`--without sandsql`, or wait for his tap"
        ),
        release(
            "tools/ci release start --targets db,restate,code-sandbox --without sandsql",
            **{"tools/ci release plan --targets db,restate,code-sandbox --without sandsql --json": planned(left_out=("sandsql",))},
        ): Allow(),
        release("tools/ci release start --targets db,restate,code-sandbox", planned("sandsql"), (OTHER_CARD,)): Allow(),
        release("tools/ci release start --targets db,restate,code-sandbox", planned("sandsql"), ()): Allow(),
        release("tools/ci release start --targets db,restate,code-sandbox", planned("sandsql"), (TITLED_CARD,)): Block(pattern=r"gates: sandsql under card `card-index-order`\."),
        release("tools/ci release start --targets db,restate,code-sandbox --json", planned("sandsql")): Block(),
        release("./tools/ci release start sand", **{"./tools/ci release plan sand --json": planned(picked=("sand",))}): Allow(),
        release("tools/ci release hotfix 48885 48890", **{"tools/ci release plan --hotfix 48885,48890 --json": planned("sandsql", picked=())}): Block(pattern=r"`--without sandsql`"),
        release("tools/ci release start --targets db,restate,code-sandbox", planned("sandsql"), stored=(SAND_NOT_MIGRATED,)): Allow(),
        release("tools/ci release start --targets db,restate,code-sandbox", planned("sandsql"), env=DRIVE_ENV): Allow(),
        release("tools/ci release start --targets db,restate,code-sandbox --dry-run"): Allow(),
        release("tools/ci release start --targets db,restate,code-sandbox", (1, "ci release plan refuses: operator unavailable")): Warn(
            pattern=r"^Warning, nothing was blocked: release_cards could not read the release plan[\s\S]*operator unavailable"
        ),
        release("tools/ci release start --targets db,restate,code-sandbox", planned("sandsql"), **{BOARD: (1, "cc-present down")}): Warn(pattern=r"could not read the owner's board[\s\S]*cc-present down"),
        release("tools/ci release start --targets db,restate,code-sandbox", planned("sandsql"), **{" ".join(INCIDENT_DIGEST): (1, "cci: daemon unavailable")}): Warn(pattern=r"could not read whether an incident is open"),
        release("tools/ci release status"): Allow(),
    },
)
def release_leaves_out_what_his_open_cards_gate(evt: BaseHookEvent) -> HookResult | None:
    calls = [call for call in evt.command.calls("ci") if call.args[:1] == ("release",) and call.args[1:2] and call.args[1] in RELEASE_VERBS and "--dry-run" not in call.args]
    if not calls or not drive_session(evt):
        return None
    try:
        if open_incidents(evt, read_incident_source):
            return None
    except (OSError, subprocess.SubprocessError, ValueError, KeyError) as failed:
        return unread(evt, "whether an incident is open", failed)
    if (entry := drive_entry(evt)) is None:
        return unread(evt, "the owner's board", "this session names no registered drive")
    try:
        cards = list({card["block"]: card for card in json.loads(read([str(LR), "board", "waits", "--drive", bus_name(entry), "--json"], BOARD_SECONDS)) if card["kind"] == "pick"}.values())
    except (Unread, ValueError, KeyError) as failed:
        return unread(evt, "the owner's board", failed)
    for call in calls:
        try:
            found = gating(shipped(call, run_dir(evt, call)), cards)
        except (Unread, UnknownDirectory, ValueError, KeyError) as failed:
            return unread(evt, "the release plan", failed)
        if found:
            gated = "; ".join(GATED.format(stack=stack, card=card["block"]) for stack, card in found)
            return evt.block(REFUSAL.format(gated=gated, stacks=",".join(dict.fromkeys(stack for stack, _ in found))))
    return None
