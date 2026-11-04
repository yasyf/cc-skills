from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

from captain_hook import Allow, BaseHookEvent, Block, Call, Event, HookResult, Input, Tool, on
from captain_hook.util import reqenv

from .call_dir import UnknownDirectory, run_dir
from .compaction_handoff import Bound, CompactionState, bus_name

BOUND = [CompactionState(active=True, coordinator=True)]

LR = Path(__file__).parents[2] / "bin" / "lr"
DRIVE = "durable-agents-rebuild"
GATE_LANE = "switch-drive"
ROOT_LANE = "root"
NOTE_LANE = "gate5"
OVERRIDE = "GATE5_OWNER_OVERRIDE"
OWNER_LANES = frozenset({"owner", "owner-relay"})
NOT_NAMING_FLAGS = frozenset({"without", "exclude", "commit", "from"})
VALUE_FLAGS = frozenset({"commit", "from"})
NAMES_SAND_NEXT = re.compile(r"(?:^|/)sand-next(?:/|$)")
PASS = re.compile(r"GATE5 PASS ([0-9a-fA-F]{7,40})\b")
READ_SECONDS = 20
SHA = "dc94738e5a1f0b2c3d4e5f60718293a4b5c6d7e8"
OTHER = "0eab10ae6a77c2d9e1f3a5b7c9d1e3f5a7b9c1d3"
CONFIG = {"CLAUDE_CONFIG_DIR": str(Path(__file__).parent / "tests" / "fixtures" / "gate5-config")}
ROUTE = f"{LR} route {GATE_LANE}"
GREP = "cci grep"

UNGATED = (
    "GATE5: this release carries sand-next at {commit}, and switch-drive has posted no `GATE5 PASS <sha>` for that commit"
    " in the last 12 hours. Ask switch-drive for a gate run on it, or leave `sand-next` out of the start."
)
UNCHECKABLE = (
    "GATE5: a `--green` release that names sand-next names no commit the gate can check against `GATE5 PASS <sha>` records."
    " Leave `sand-next` out, or name its commit with `--from <sha>` once switch-drive posts `GATE5 PASS <sha>`."
)
UNREAD = (
    "GATE5 fails closed: it could not read whether switch-drive passed this sand-next release ({why})."
    " Fix the read and retry, or leave `sand-next` out of the start."
)
NOT_OWNER = "GATE5: {OVERRIDE}={seq} names a record from `{lane}`, and only an owner record from `owner` or `owner-relay` overrides the gate."
OVERRIDDEN = "GATE5 OVERRIDE: `{command}` carries sand-next without a gate pass, under owner record #{seq}."


class Unread(Exception):
    pass


def read(argv: list[str], cwd: str | None = None) -> str:
    try:
        done = subprocess.run(argv, capture_output=True, text=True, timeout=READ_SECONDS, cwd=cwd, env=dict(reqenv.env_map()))
    except (OSError, subprocess.TimeoutExpired) as failed:
        raise Unread(f"`{' '.join(argv[:2])}`: {failed}") from failed
    if done.returncode:
        raise Unread(f"`{' '.join(argv[:2])}` exited {done.returncode}: {done.stderr.strip()}")
    return done.stdout


def release_flags(args: tuple[str, ...]) -> dict[str, list[str]]:
    flags: dict[str, list[str]] = {}
    words = iter(args)
    for word in words:
        if not word.startswith("--"):
            continue
        name, inline, value = word[2:].partition("=")
        flags.setdefault(name, [])
        if name in VALUE_FLAGS:
            flags[name].append(value if inline else next(words, ""))
    return flags


def carries_sand_next(args: tuple[str, ...]) -> bool:
    words = iter(args)
    for word in words:
        flag, inline, value = word[2:].partition("=") if word.startswith("--") else ("", "", word)
        if flag in NOT_NAMING_FLAGS:
            if not inline:
                next(words, "")
            continue
        if any(NAMES_SAND_NEXT.search(name.strip()) for name in value.split(",")):
            return True
    return False


def released_commit(flags: dict[str, list[str]], cwd: str) -> tuple[str, str]:
    ref = (flags.get("commit") or flags.get("from") or [""])[-1]
    if not ref and "manual" not in flags and "local" not in flags:
        dev = read(["git", "ls-remote", "--heads", "origin", "refs/heads/dev"], cwd).split()
        if not dev:
            raise Unread("origin has no dev branch")
        return dev[0].lower(), "origin dev's tip"
    ref = ref or "HEAD"
    return read(["git", "rev-parse", "--verify", f"{ref}^{{commit}}"], cwd).strip().lower(), f"`{ref}`"


def gate_lanes() -> list[str]:
    drives = Path(reqenv.getenv("CLAUDE_CONFIG_DIR") or Path.home() / ".claude") / "long-running" / "drives"
    entry = next((entry for path in sorted(drives.glob("*.json")) if bus_name(entry := json.loads(path.read_text())) == DRIVE), None)
    if entry is None:
        raise Unread(f"no drive {DRIVE} is registered under {drives}")
    routed = json.loads(read([str(LR), "route", GATE_LANE, "--config", str(Path(entry["state_dir"]) / "lr.json")]))["route"]
    return list(dict.fromkeys(lane for lane in (GATE_LANE, routed) if lane != ROOT_LANE))


def gate_passed(sha: str) -> bool:
    lanes = [arg for lane in gate_lanes() for arg in ("--lane", lane)]
    said = read(["cci", "grep", "--drive", DRIVE, *lanes, "--since", "12h", "--json", "--budget", "262144", "-F", "--ignore-case=false", "--", "GATE5 PASS"])
    return any((found := PASS.match(json.loads(line)["text"])) and sha.startswith(found[1].lower()) for line in said.splitlines() if line.strip())


def overridden(call: Call, seq: str) -> str | None:
    record = json.loads(read(["cci", "show", seq.lstrip("#"), "--json"]).splitlines()[0])
    if record["lane"] not in OWNER_LANES:
        return NOT_OWNER.format(OVERRIDE=OVERRIDE, seq=seq, lane=record["lane"])
    read(["cci", "post", "--drive", DRIVE, "--lane", NOTE_LANE, "--kind", "note", "--to", ROOT_LANE, "--text", OVERRIDDEN.format(command=call.command.raw, seq=seq.lstrip("#"))])
    return None


def gated(evt: BaseHookEvent, call: Call) -> str | None:
    if not carries_sand_next(call.args[2:]):
        return None
    flags = release_flags(call.args[2:])
    if seq := dict(call.command.env).get(OVERRIDE):
        return overridden(call, seq)
    if "green" in flags:
        return UNCHECKABLE
    sha, commit = released_commit(flags, run_dir(evt, call))
    return None if gate_passed(sha) else UNGATED.format(commit=commit)



def passes(sha: str, lane: str = GATE_LANE) -> str:
    return json.dumps({"seq": 357100, "lane": lane, "kind": "report", "text": f"GATE5 PASS {sha[:12]} sand-next scenarios green"})


def release(command: str, said: str | tuple[int, str] = "", routed: str = GATE_LANE, env: dict[str, str] = CONFIG, **commands: str | tuple[int, str]) -> Input:
    stubs = {
        "git ls-remote": f"{SHA}\trefs/heads/dev\n",
        "git rev-parse": f"{SHA}\n",
        ROUTE: json.dumps({"lane": GATE_LANE, "route": routed, "state": "desired", "standing": ""}),
        f"{GREP} --drive {DRIVE} --lane {GATE_LANE}" + (f" --lane {routed}" if routed not in (GATE_LANE, ROOT_LANE) else " --since"): said,
    }
    return Input(command=command, env=env, commands=stubs | commands, state=BOUND)


@on(
    Event.PreToolUse,
    only_if=[Bound(), Tool("Bash")],
    tests={
        Input(command="tools/ci release start sand --without=web,sand-next,api", state=BOUND): Allow(),
        Input(command="tools/ci release start --groups platform --without sand-next", state=BOUND): Allow(),
        Input(command="tools/ci release start sand --only api,web", state=BOUND): Allow(),
        Input(command="tools/ci release start --manual --stack api/plat-usw2-prod --commit dc94738", state=BOUND): Allow(),
        Input(command="tools/ci release hotfix 48885 --without sand-next", state=BOUND): Allow(),
        Input(command="tools/ci release status", state=BOUND): Allow(),
        release("tools/ci release start sand-next"): Block(pattern=r"^GATE5: this release carries sand-next at origin dev's tip, and switch-drive has posted no"),
        Input(command="./tools/ci release start sand --without sand-next/core-usw2-auto", state=BOUND): Allow(),
        Input(command="tools/ci release start sand", state=BOUND): Allow(),
        Input(command="tools/ci release start everything", state=BOUND): Allow(),
        Input(command="tools/ci release start --hotfix 49538", state=BOUND): Allow(),
        Input(command="tools/ci release hotfix 49538 --dry-run", state=BOUND): Allow(),
        Input(command="tools/ci release hotfix --help", state=BOUND): Allow(),
        release("tools/ci release start --only sand-next,api"): Block(pattern=r"Ask switch-drive for a gate run"),
        release("tools/ci release start --manual sand-next --env core-usw2-auto --commit dc94738", passes(OTHER)): Block(pattern=r"^GATE5: this release carries sand-next at `dc94738`,"),
        release("tools/ci release start sand-next", passes(SHA)): Allow(),
        Input(command=". /env.sh; cd $A; tools/ci release start sand-next", cwd="/", env=CONFIG, state=BOUND): Block(pattern=r"^GATE5 fails closed: .*`cd` this hook cannot follow"),
        Input(command=". /env.sh; cd $A; tools/ci release start sand --without sand-next/core-usw2-auto", cwd="/", env=CONFIG, state=BOUND): Allow(),
        release("cd /repo && ci release start --targets sand-next --from dc94738", passes(SHA)): Allow(),
        release("tools/ci release start sand-next", passes(OTHER)): Block(pattern=r"^GATE5: this release carries sand-next"),
        release("tools/ci release start sand-next", passes(SHA, "switch-drive-2"), routed="switch-drive-2"): Allow(),
        release("tools/ci release start sand-next", passes(SHA), routed=ROOT_LANE): Allow(),
        release("tools/ci release start --manual --stack sand-next/core-usw2-auto", passes(OTHER)): Block(pattern=r"^GATE5: this release carries sand-next at `HEAD`,"),
        release("tools/ci release start sand-next", (1, "cci: daemon unavailable")): Block(pattern=r"^GATE5 fails closed: it could not read[\s\S]*daemon unavailable"),
        release("tools/ci release start sand-next", **{"git ls-remote": (128, "fatal: could not read from remote")}): Block(pattern=r"^GATE5 fails closed[\s\S]*could not read from remote"),
        release("tools/ci release start sand-next", env={"CLAUDE_CONFIG_DIR": "/nonexistent"}): Block(pattern=r"no drive durable-agents-rebuild is registered"),
        Input(command="ci release hotfix 48885", state=BOUND): Allow(),
        release("tools/ci release start sand-next --green"): Block(pattern=r"^GATE5: a `--green` release"),
        release(
            "GATE5_OWNER_OVERRIDE=356990 tools/ci release start sand-next",
            **{"cci show 356990": json.dumps({"seq": 356990, "lane": "owner-relay", "kind": "answer", "text": "ship sand-next now"}), "cci post": "#357200"}
        ): Allow(),
        release("GATE5_OWNER_OVERRIDE=356990 tools/ci release start sand-next", **{"cci show 356990": json.dumps({"seq": 356990, "lane": "release-6", "kind": "note", "text": "x"})}): Block(
            pattern=r"names a record from `release-6`"
        ),
        release("GATE5_OWNER_OVERRIDE=356990 tools/ci release start sand-next", **{"cci show 356990": (1, "no record has this number")}): Block(pattern=r"could not read"),
    },
)
def sand_next_release_needs_gate5_pass(evt: BaseHookEvent) -> HookResult | None:
    for call in evt.command.calls("ci"):
        if call.args[:1] != ("release",) or call.args[1:2] == () or call.args[1] != "start":
            continue
        try:
            refusal = gated(evt, call)
        except (Unread, UnknownDirectory, ValueError, KeyError) as failed:
            refusal = UNREAD.format(why=failed)
        if refusal:
            return evt.block(refusal)
    return None
